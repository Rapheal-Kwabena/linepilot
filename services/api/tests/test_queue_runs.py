"""At-least-once queue delivery, cancellation, and local-mode regressions."""

from __future__ import annotations

import asyncio
import threading
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import sessionmaker
from vercel.queue import RetryAfter
from vercel.queue.embedded import embedded_queue_service

from app import main as api_main
from app.api.runs import cancel_schedule, create_schedule, reconcile_runs
from app.core.db import Base, get_db
from app.core.http import ApiError
from app.engineering import schedule
from app.main import app
from app.models import Facility, ScheduledOperation, ScheduleRun, SimulationEvent, SimulationRun, utc_now
from app.queue_messages import RUN_TOPIC, RunMessage
from app.queue_subscriber import process_run
from app.schemas import ScheduleCreate
from app.seed import seed_facility
from app.snapshot import build_snapshot
from app.workers import queue_runs
from app.workers import runs as local_runs


@pytest.fixture
def queue_database(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'queue.sqlite'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(queue_runs, "SessionLocal", sessions)
    monkeypatch.setenv("LINEPILOT_RUN_BACKEND", "vercel_queue")
    with sessions() as db:
        facility = seed_facility(db, anchor=date(2026, 11, 16))
        facility_id = facility.id
    yield sessions, facility_id
    engine.dispose()


def _queued_schedule(sessions, facility_id, *, backend="vercel_queue"):
    with sessions() as db:
        facility = db.get(Facility, facility_id)
        snapshot = build_snapshot(db, facility)
        run = ScheduleRun(
            facility_id=facility_id,
            name="Queue test",
            algorithm="fcfs",
            objective="makespan",
            input_snapshot=snapshot,
            solver_configuration={"time_limit_seconds": 3, "random_seed": 11, "search_workers": 1},
            execution_backend=backend,
            status="queued",
            metrics={},
            diagnostics=[],
        )
        db.add(run)
        db.commit()
        return run.id, snapshot


def test_vercel_requires_queue_backend(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("LINEPILOT_RUN_BACKEND", raising=False)
    with pytest.raises(RuntimeError, match="vercel_queue is required on Vercel"):
        queue_runs.run_backend()
    monkeypatch.setenv("LINEPILOT_RUN_BACKEND", "local")
    with pytest.raises(RuntimeError, match="vercel_queue is required on Vercel"):
        queue_runs.run_backend()
    monkeypatch.setenv("LINEPILOT_RUN_BACKEND", "vercel_queue")
    assert queue_runs.run_backend() == "vercel_queue"
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("LINEPILOT_RUN_BACKEND", raising=False)
    assert queue_runs.run_backend() == "local"


def test_queue_schedule_redelivery_is_idempotent(queue_database):
    sessions, facility_id = queue_database
    run_id, _ = _queued_schedule(sessions, facility_id)
    assert queue_runs.process_queued_run("schedule", run_id) == "ack"
    with sessions() as db:
        completed = db.get(ScheduleRun, run_id)
        assert completed.status == "completed"
        assert completed.attempt_count == 1
        assert len(completed.assignments) == 60
        assert completed.claim_token is None
        assert completed.lease_expires_at is None
    assert queue_runs.process_queued_run("schedule", run_id) == "ack"
    with sessions() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(ScheduledOperation)
                .where(ScheduledOperation.schedule_run_id == run_id)
            )
            == 60
        )
        assert db.get(ScheduleRun, run_id).attempt_count == 1


def test_sdk_embedded_subscriber_delivers_schedule(queue_database):
    sessions, facility_id = queue_database
    run_id, _ = _queued_schedule(sessions, facility_id)

    async def deliver():
        async with embedded_queue_service() as service:
            await service.get_async_client().send(RUN_TOPIC, RunMessage(kind="schedule", run_id=run_id))
            for _ in range(100):
                await asyncio.sleep(0.05)
                with sessions() as db:
                    status = db.get(ScheduleRun, run_id).status
                if status not in {"queued", "running"}:
                    return status
            return status

    assert asyncio.run(deliver()) == "completed"
    with sessions() as db:
        assert db.get(ScheduleRun, run_id).attempt_count == 1


def test_queue_simulation_redelivery_does_not_duplicate_events(queue_database):
    sessions, facility_id = queue_database
    schedule_id, snapshot = _queued_schedule(sessions, facility_id)
    assert queue_runs.process_queued_run("schedule", schedule_id) == "ack"
    with sessions() as db:
        run = SimulationRun(
            schedule_run_id=schedule_id,
            random_seed=7,
            configuration={
                "mode": "deterministic",
                "random_seed": 7,
                "horizon_minutes": snapshot["horizon_minutes"],
            },
            execution_backend="vercel_queue",
            status="queued",
            summary_metrics={},
            diagnostics=[],
        )
        db.add(run)
        db.commit()
        run_id = run.id
    assert queue_runs.process_queued_run("simulation", run_id) == "ack"
    with sessions() as db:
        completed = db.get(SimulationRun, run_id)
        assert completed.status == "completed"
        event_count = len(completed.events)
        assert event_count > 0
        assert completed.attempt_count == 1
    assert queue_runs.process_queued_run("simulation", run_id) == "ack"
    with sessions() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(SimulationEvent)
                .where(SimulationEvent.simulation_run_id == run_id)
            )
            == event_count
        )


def test_duplicate_claim_retries_and_old_attempt_cannot_save(queue_database):
    sessions, facility_id = queue_database
    run_id, snapshot = _queued_schedule(sessions, facility_id)
    first = queue_runs._claim("schedule", run_id)
    assert isinstance(first, queue_runs.ClaimedRun)
    assert queue_runs._claim("schedule", run_id) == "retry"
    with sessions.begin() as db:
        db.execute(
            update(ScheduleRun)
            .where(ScheduleRun.id == run_id)
            .values(lease_expires_at=utc_now() - timedelta(seconds=1))
        )
    second = queue_runs._claim("schedule", run_id)
    assert isinstance(second, queue_runs.ClaimedRun)
    assert second.token != first.token
    result = schedule(snapshot, "fcfs")
    assert queue_runs._finish(first, result) == "retry"
    with sessions() as db:
        still_running = db.get(ScheduleRun, run_id)
        assert still_running.status == "running"
        assert still_running.assignments == []
    assert queue_runs._finish(second, result) == "ack"
    with sessions() as db:
        completed = db.get(ScheduleRun, run_id)
        assert completed.status == "completed"
        assert completed.attempt_count == 2
        assert len(completed.assignments) == 60


def test_cancellation_before_delivery_and_during_final_save(queue_database):
    sessions, facility_id = queue_database
    queued_id, _ = _queued_schedule(sessions, facility_id)
    assert queue_runs.cancel_queue_run("schedule", queued_id)
    assert queue_runs.process_queued_run("schedule", queued_id) == "ack"
    with sessions() as db:
        queued = db.get(ScheduleRun, queued_id)
        assert queued.status == "cancelled"
        assert queued.attempt_count == 0
        assert queued.assignments == []

    running_id, snapshot = _queued_schedule(sessions, facility_id)
    claim = queue_runs._claim("schedule", running_id)
    assert isinstance(claim, queue_runs.ClaimedRun)
    assert queue_runs.cancel_queue_run("schedule", running_id)
    assert queue_runs._finish(claim, schedule(snapshot, "fcfs")) == "ack"
    with sessions() as db:
        running = db.get(ScheduleRun, running_id)
        assert running.status == "cancelled"
        assert running.assignments == []


def test_running_queue_cancellation_stops_engine(queue_database, monkeypatch):
    sessions, facility_id = queue_database
    run_id, _ = _queued_schedule(sessions, facility_id)
    entered = threading.Event()
    outcomes = []

    def waiting_schedule(snapshot, *, cancel_event, **settings):  # noqa: ARG001
        entered.set()
        assert cancel_event.wait(3)
        return {"status": "cancelled", "solver_status": "CANCELLED", "diagnostics": []}

    monkeypatch.setattr("app.engineering.schedule", waiting_schedule)
    worker = threading.Thread(
        target=lambda: outcomes.append(queue_runs.process_queued_run("schedule", run_id))
    )
    worker.start()
    assert entered.wait(3)
    assert queue_runs.cancel_queue_run("schedule", run_id)
    worker.join(timeout=4)
    assert not worker.is_alive()
    assert outcomes == ["ack"]
    with sessions() as db:
        assert db.get(ScheduleRun, run_id).status == "cancelled"


def test_running_queue_cancellation_stops_simulation(queue_database, monkeypatch):
    sessions, facility_id = queue_database
    schedule_id, snapshot = _queued_schedule(sessions, facility_id)
    assert queue_runs.process_queued_run("schedule", schedule_id) == "ack"
    with sessions() as db:
        simulation = SimulationRun(
            schedule_run_id=schedule_id,
            random_seed=4,
            configuration={
                "mode": "deterministic",
                "random_seed": 4,
                "horizon_minutes": snapshot["horizon_minutes"],
            },
            execution_backend="vercel_queue",
            status="queued",
            summary_metrics={},
            diagnostics=[],
        )
        db.add(simulation)
        db.commit()
        simulation_id = simulation.id
    entered = threading.Event()
    outcomes = []

    def waiting_simulation(snapshot, assignments, config, *, cancel_event):  # noqa: ARG001
        entered.set()
        assert cancel_event.wait(3)
        return {"status": "cancelled", "diagnostics": []}

    monkeypatch.setattr("app.engineering.simulate", waiting_simulation)
    worker = threading.Thread(
        target=lambda: outcomes.append(queue_runs.process_queued_run("simulation", simulation_id))
    )
    worker.start()
    assert entered.wait(3)
    assert queue_runs.cancel_queue_run("simulation", simulation_id)
    worker.join(timeout=4)
    assert not worker.is_alive()
    assert outcomes == ["ack"]
    with sessions() as db:
        run = db.get(SimulationRun, simulation_id)
        assert run.status == "cancelled"
        assert run.events == []


def test_retry_limit_becomes_visible_terminal_failure(queue_database):
    sessions, facility_id = queue_database
    run_id, _ = _queued_schedule(sessions, facility_id)
    with sessions.begin() as db:
        db.execute(
            update(ScheduleRun).where(ScheduleRun.id == run_id).values(attempt_count=queue_runs.MAX_ATTEMPTS)
        )
    assert queue_runs.process_queued_run("schedule", run_id) == "ack"
    with sessions() as db:
        run = db.get(ScheduleRun, run_id)
        assert run.status == "failed"
        assert "repeated worker attempts" in run.diagnostics[0]


def test_reconciler_republishes_only_abandoned_queue_rows(queue_database, monkeypatch):
    sessions, facility_id = queue_database
    stale_id, _ = _queued_schedule(sessions, facility_id)
    recent_id, _ = _queued_schedule(sessions, facility_id)
    local_id, _ = _queued_schedule(sessions, facility_id, backend="local")
    with sessions.begin() as db:
        db.execute(
            update(ScheduleRun)
            .where(ScheduleRun.id == stale_id)
            .values(created_at=utc_now() - timedelta(minutes=2))
        )
    sent = []
    monkeypatch.setattr(queue_runs, "_send_message", lambda kind, run_id: sent.append((kind, run_id)))
    assert queue_runs.reconcile_queue_runs() == {"attempted": 1, "published": 1, "failed": 0}
    assert sent == [("schedule", stale_id)]
    with sessions() as db:
        assert db.get(ScheduleRun, stale_id).last_enqueued_at is not None
        assert db.get(ScheduleRun, recent_id).last_enqueued_at is None
        assert db.get(ScheduleRun, local_id).last_enqueued_at is None


def test_overlapping_reconciliation_only_publishes_once(queue_database, monkeypatch):
    sessions, facility_id = queue_database
    run_id, _ = _queued_schedule(sessions, facility_id)
    with sessions.begin() as db:
        db.execute(
            update(ScheduleRun)
            .where(ScheduleRun.id == run_id)
            .values(created_at=utc_now() - timedelta(minutes=2))
        )
    entered = threading.Event()
    release = threading.Event()
    sent = []
    results = []

    def slow_send(kind, published_id):
        sent.append((kind, published_id))
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(queue_runs, "_send_message", slow_send)
    first = threading.Thread(target=lambda: results.append(queue_runs.reconcile_queue_runs()))
    first.start()
    try:
        assert entered.wait(3)
        assert queue_runs.reconcile_queue_runs() == {"attempted": 0, "published": 0, "failed": 0}
    finally:
        release.set()
        first.join(timeout=4)
    assert not first.is_alive()
    assert results == [{"attempted": 1, "published": 1, "failed": 0}]
    assert sent == [("schedule", run_id)]


def test_failed_republication_releases_reservation(queue_database, monkeypatch):
    sessions, facility_id = queue_database
    run_id, _ = _queued_schedule(sessions, facility_id)
    with sessions.begin() as db:
        db.execute(
            update(ScheduleRun)
            .where(ScheduleRun.id == run_id)
            .values(created_at=utc_now() - timedelta(minutes=2))
        )

    def reject(kind, published_id):  # noqa: ARG001
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(queue_runs, "_send_message", reject)
    assert queue_runs.reconcile_queue_runs() == {"attempted": 1, "published": 0, "failed": 1}
    with sessions() as db:
        run = db.get(ScheduleRun, run_id)
        assert run.status == "queued"
        assert run.last_enqueued_at is None


def test_reconciler_republishes_expired_running_claim(queue_database, monkeypatch):
    sessions, facility_id = queue_database
    run_id, _ = _queued_schedule(sessions, facility_id)
    claim = queue_runs._claim("schedule", run_id)
    assert isinstance(claim, queue_runs.ClaimedRun)
    with sessions.begin() as db:
        db.execute(
            update(ScheduleRun)
            .where(ScheduleRun.id == run_id)
            .values(lease_expires_at=utc_now() - timedelta(seconds=1))
        )
    sent = []
    monkeypatch.setattr(queue_runs, "_send_message", lambda kind, run_id: sent.append((kind, run_id)))
    assert queue_runs.reconcile_queue_runs() == {"attempted": 1, "published": 1, "failed": 0}
    assert sent == [("schedule", run_id)]


def test_republication_can_create_a_new_queue_message(queue_database, monkeypatch):
    sessions, facility_id = queue_database
    run_id, _ = _queued_schedule(sessions, facility_id)
    messages = []
    monkeypatch.setattr(
        "vercel.queue.sync.send",
        lambda topic, payload, **options: messages.append((topic, payload, options)),
    )
    queue_runs.publish_run("schedule", run_id)
    queue_runs.publish_run("schedule", run_id)
    assert len(messages) == 2
    assert all(message.kind == "schedule" and message.run_id == run_id for _, message, _ in messages)
    assert all(options == {} for _, _, options in messages)


def test_local_startup_recovery_never_invalidates_queue_work(queue_database, monkeypatch):
    sessions, facility_id = queue_database
    queue_id, _ = _queued_schedule(sessions, facility_id)
    local_id, _ = _queued_schedule(sessions, facility_id, backend="local")
    monkeypatch.setattr(local_runs, "SessionLocal", sessions)
    local_runs.recover_interrupted_runs()
    with sessions() as db:
        assert db.get(ScheduleRun, queue_id).status == "queued"
        assert db.get(ScheduleRun, local_id).status == "failed"


def test_queue_api_startup_does_not_seed_or_fail_active_runs(queue_database, monkeypatch):
    sessions, facility_id = queue_database
    run_id, _ = _queued_schedule(sessions, facility_id)
    monkeypatch.setattr(api_main, "engine", sessions.kw["bind"])
    monkeypatch.setattr(api_main, "DATABASE_URL", "sqlite:///queue-test.sqlite")
    monkeypatch.setattr(api_main, "SessionLocal", sessions)
    monkeypatch.setattr(api_main, "seed_facility", lambda db: pytest.fail("queue startup must not seed"))
    monkeypatch.setattr(
        api_main, "recover_interrupted_runs", lambda: pytest.fail("queue startup must not fail active runs")
    )
    monkeypatch.delenv("AUTO_SEED", raising=False)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    with sessions() as db:
        assert db.get(ScheduleRun, run_id).status == "queued"


def test_queue_api_returns_queued_run_and_persisted_cancel(queue_database, monkeypatch):
    sessions, facility_id = queue_database
    sent = []
    monkeypatch.setattr(queue_runs, "_send_message", lambda kind, run_id: sent.append((kind, run_id)))
    monkeypatch.setattr("app.api.runs.launch", lambda *args: pytest.fail("local worker must not start"))
    with sessions() as db:
        created = create_schedule(ScheduleCreate(facility_id=facility_id, algorithm="fcfs"), db)
        assert created["status"] == "queued"
        assert sent == [("schedule", created["id"])]
        assert db.get(ScheduleRun, created["id"]).execution_backend == "vercel_queue"
        cancelled = cancel_schedule(created["id"], db)
        assert cancelled["status"] == "cancelled"
    assert queue_runs.process_queued_run("schedule", created["id"]) == "ack"


def test_queue_http_schedule_and_simulation_round_trip(queue_database, monkeypatch):
    sessions, facility_id = queue_database
    sent = []
    monkeypatch.setattr(queue_runs, "_send_message", lambda kind, run_id: sent.append((kind, run_id)))

    def test_session():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = test_session
    client = TestClient(app)
    try:
        created = client.post("/api/v1/schedules", json={"facility_id": facility_id, "algorithm": "fcfs"})
        assert created.status_code == 202
        schedule_id = created.json()["id"]
        assert created.json()["status"] == "queued"
        assert client.get(f"/api/v1/schedules/{schedule_id}").json()["status"] == "queued"
        assert sent == [("schedule", schedule_id)]

        assert queue_runs.process_queued_run("schedule", schedule_id) == "ack"
        schedule_result = client.get(f"/api/v1/schedules/{schedule_id}")
        assert schedule_result.status_code == 200
        assert schedule_result.json()["status"] == "completed"
        assert schedule_result.json()["assignment_count"] == 60

        simulation = client.post("/api/v1/simulations", json={"schedule_run_id": schedule_id})
        assert simulation.status_code == 202
        simulation_id = simulation.json()["id"]
        assert simulation.json()["status"] == "queued"
        assert sent[-1] == ("simulation", simulation_id)

        assert queue_runs.process_queued_run("simulation", simulation_id) == "ack"
        simulation_result = client.get(f"/api/v1/simulations/{simulation_id}")
        assert simulation_result.status_code == 200
        assert simulation_result.json()["status"] == "completed"
        assert simulation_result.json()["event_count"] > 0
    finally:
        client.close()
        app.dependency_overrides.pop(get_db, None)


def test_queue_publish_failure_is_saved_as_failed(queue_database, monkeypatch):
    sessions, facility_id = queue_database

    def reject(kind, run_id):  # noqa: ARG001
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(queue_runs, "_send_message", reject)
    with sessions() as db:
        with pytest.raises(ApiError) as error:
            create_schedule(ScheduleCreate(facility_id=facility_id, algorithm="fcfs"), db)
        assert error.value.status_code == 503
        db.expire_all()
        run = db.scalar(select(ScheduleRun).where(ScheduleRun.execution_backend == "vercel_queue"))
        assert run.status == "failed"
        assert "could not accept" in run.diagnostics[0]


def test_queue_subscriber_and_reconciler_secret_fail_closed(monkeypatch):
    monkeypatch.setattr("app.queue_subscriber.process_queued_run", lambda kind, run_id: "retry")
    message = SimpleNamespace(payload=SimpleNamespace(kind="schedule", run_id="run"))
    with pytest.raises(RetryAfter):
        asyncio.run(process_run(message))
    monkeypatch.delenv("CRON_SECRET", raising=False)
    with pytest.raises(ApiError) as missing:
        reconcile_runs(SimpleNamespace(headers={}))
    assert missing.value.status_code == 401
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    monkeypatch.setattr("app.api.runs.reconcile_queue_runs", lambda: {"attempted": 0})
    assert reconcile_runs(SimpleNamespace(headers={"authorization": "Bearer test-secret"})) == {
        "attempted": 0
    }
    client = TestClient(app)
    try:
        assert client.get("/api/v1/internal/reconcile-runs").status_code == 405
        assert client.post("/api/v1/internal/reconcile-runs").status_code == 401
        authorized = client.post(
            "/api/v1/internal/reconcile-runs", headers={"Authorization": "Bearer test-secret"}
        )
        assert authorized.status_code == 200
        assert authorized.json() == {"attempted": 0}
    finally:
        client.close()
