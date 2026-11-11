"""Persistence and snapshot invariants beyond the pure solver tests."""

from __future__ import annotations

import queue
import threading
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.imports import _validate, confirm_import
from app.api.resources import bootstrap, update_calendar
from app.api.runs import create_schedule, evaluate_scenario, get_simulation_events
from app.core.db import Base
from app.core.http import ApiError
from app.models import (
    Facility,
    Machine,
    OperatingCalendar,
    ProductionJob,
    Scenario,
    ScheduleRun,
    SimulationEvent,
    SimulationRun,
)
from app.scenario_entities import materialize_virtual_entities
from app.schemas import (
    CalendarCreate,
    CalendarUpdate,
    ImportRequest,
    MachineCreate,
    ScenarioEvaluate,
    ScheduleCreate,
)
from app.seed import seed_facility
from app.serializers import serialize_schedule
from app.snapshot import (
    _calendar_availability,
    _calendar_closures,
    _subtract,
    apply_modifications,
    build_snapshot,
)
from app.workers import runs as run_worker


def seeded_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine)
    facility = seed_facility(db, anchor=date(2026, 11, 16))
    return db, facility


def test_seed_utc_release_and_maintenance_match_chicago_calendar():
    db, facility = seeded_session()
    try:
        snapshot = build_snapshot(db, facility)
        assert snapshot["origin"] == "2026-11-16T12:00:00Z"  # 06:00 CST
        assert len(snapshot["jobs"]) == 20
        assert len(snapshot["machines"]) == 6
        assert snapshot["jobs"][0]["release_min"] == 0
        press = next(machine for machine in snapshot["machines"] if machine["code"] == "M-03")
        assert press["availability"][0] == [0, 960]
        assert press["downtime"] == [[300, 480]]  # 11:00–14:00 CST
        stored_job = db.scalar(select(ProductionJob).where(ProductionJob.job_number == "JOB-101"))
        assert stored_job.release_time.tzinfo is not None
        assert stored_job.release_time.isoformat() == "2026-11-16T12:00:00+00:00"
    finally:
        db.close()


def test_bootstrap_sample_notice_tracks_selected_facility():
    db, sample = seeded_session()
    try:
        custom = Facility(
            workspace_id=sample.workspace_id,
            facility_code="CUSTOM",
            facility_name="Custom Plant",
            timezone="America/Chicago",
            status="active",
            sample_data=False,
        )
        db.add(custom)
        db.commit()
        assert bootstrap(facility_id=sample.id, db=db)["sample_data_notice"]
        selected = bootstrap(facility_id=custom.id, db=db)
        assert selected["facility"]["facility_code"] == "CUSTOM"
        assert selected["sample_data_notice"] is None
    finally:
        db.close()


def test_schedule_solver_settings_validate_worker_bounds():
    assert ScheduleCreate(facility_id="facility").search_workers == 1
    assert ScenarioEvaluate().search_workers == 1
    assert ScheduleCreate(facility_id="facility", search_workers=8).search_workers == 8
    assert ScenarioEvaluate(search_workers=8).search_workers == 8
    for invalid_workers in (0, 9, True, 1.5, "2"):
        with pytest.raises(ValidationError):
            ScheduleCreate(facility_id="facility", search_workers=invalid_workers)
        with pytest.raises(ValidationError):
            ScenarioEvaluate(search_workers=invalid_workers)


def test_schedule_and_scenario_evaluation_persist_solver_assumptions(monkeypatch):
    db, facility = seeded_session()
    try:
        launches = []
        monkeypatch.setattr("app.api.runs.launch", lambda *args: launches.append(args))
        settings = {"time_limit_seconds": 4.5, "random_seed": 77, "search_workers": 2}
        response = create_schedule(
            ScheduleCreate(
                facility_id=facility.id,
                name="Night shift plan",
                algorithm="cp_sat",
                **settings,
            ),
            db,
        )
        assert response["name"] == "Night shift plan"
        assert response["solver_configuration"] == settings
        assert launches[0][0] == "schedule"
        assert {key: launches[0][3][key] for key in settings} == settings
        db.expire_all()
        stored = db.get(ScheduleRun, response["id"])
        assert stored.name == "Night shift plan"
        assert stored.solver_configuration == settings
        assert serialize_schedule(stored, include_assignments=False)["solver_configuration"] == settings

        scenario = Scenario(
            facility_id=facility.id,
            name="Capacity experiment",
            description="",
            input_snapshot=build_snapshot(db, facility),
            modifications=[],
        )
        db.add(scenario)
        db.commit()
        evaluated = evaluate_scenario(
            scenario.id,
            ScenarioEvaluate(algorithm="cp_sat", **settings),
            db,
        )
        assert evaluated["solver_configuration"] == settings
        assert launches[1][3]["search_workers"] == 2
        db.expire_all()
        assert db.get(ScheduleRun, evaluated["id"]).solver_configuration == settings
    finally:
        db.close()


def test_overnight_shift_expands_correctly_across_dst_changes():
    spring_calendar = SimpleNamespace(
        timezone="America/Chicago",
        weekly_shifts=[{"day": 5, "start": "22:00", "end": "06:00"}],
        holiday_exceptions=[],
    )
    spring_origin = datetime(2026, 3, 8, 4, 0, tzinfo=timezone.utc)  # Sat 22:00 CST
    assert _calendar_availability(spring_calendar, spring_origin, 12 * 60)[0] == [0, 420]
    autumn_origin = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)  # Sat 22:00 CDT
    assert _calendar_availability(spring_calendar, autumn_origin, 12 * 60)[0] == [0, 540]


def test_holiday_closes_overnight_shift_after_midnight():
    calendar = SimpleNamespace(
        timezone="America/Chicago",
        weekly_shifts=[{"day": 6, "start": "22:00", "end": "06:00"}],
        holiday_exceptions=["2026-11-16"],
        special_closures=[],
    )
    origin = datetime(2026, 11, 16, 4, 0, tzinfo=timezone.utc)  # Sunday 22:00 CST
    available = _subtract(
        _calendar_availability(calendar, origin, 480), _calendar_closures(calendar, origin, 480)
    )
    assert available == [[0, 120]]  # Monday holiday starts at local midnight


def test_virtual_scenario_capacity_and_orders_do_not_enter_baseline():
    db, facility = seeded_session()
    try:
        baseline = build_snapshot(db, facility)
        source_machine = baseline["machines"][0]["id"]
        template_job = baseline["jobs"][0]["id"]
        scenario = Scenario(
            facility_id=facility.id,
            name="Capacity and surge",
            description="",
            input_snapshot=baseline,
            modifications=[
                {"type": "add_capacity", "source_machine_id": source_machine, "machine_code": "EXTRA"},
                {"type": "order_surge", "template_job_id": template_job, "count": 2},
            ],
        )
        db.add(scenario)
        db.flush()
        modified = apply_modifications(baseline, scenario.modifications, scenario_id=scenario.id)
        materialize_virtual_entities(db, scenario, modified)
        db.commit()
        assert len(modified["machines"]) == 7
        assert len(modified["jobs"]) == 22
        assert (
            db.scalar(select(func.count()).select_from(Machine).where(Machine.scenario_id == scenario.id))
            == 1
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(ProductionJob)
                .where(ProductionJob.scenario_id == scenario.id)
            )
            == 2
        )
        fresh_baseline = build_snapshot(db, facility)
        assert len(fresh_baseline["machines"]) == 6
        assert len(fresh_baseline["jobs"]) == 20
    finally:
        db.close()


def test_unavailable_machine_status_is_excluded_from_scheduling():
    db, facility = seeded_session()
    try:
        first = db.scalar(select(Machine).where(Machine.machine_code == "M-01"))
        second = db.scalar(select(Machine).where(Machine.machine_code == "M-04"))
        first.status = "maintenance"
        db.commit()
        snapshot = build_snapshot(db, facility)
        assert first.id not in {machine["id"] for machine in snapshot["machines"]}
        second.status = "out_of_service"
        db.commit()
        try:
            build_snapshot(db, facility)
        except ValueError as exc:
            assert "no active eligible machine" in str(exc)
        else:
            raise AssertionError("Cutting jobs must not be assigned to unavailable machines")
    finally:
        db.close()


def test_shift_closure_is_calendar_unavailable_not_machine_downtime():
    db, facility = seeded_session()
    try:
        baseline = build_snapshot(db, facility)
        machine = next(item for item in baseline["machines"] if item["code"] == "M-01")
        scenario = apply_modifications(
            baseline,
            [
                {
                    "type": "shift_adjustment",
                    "machine_id": machine["id"],
                    "mode": "close",
                    "start_time": "2026-11-16T14:00:00Z",
                    "end_time": "2026-11-16T16:00:00Z",
                }
            ],
        )
        changed = next(item for item in scenario["machines"] if item["id"] == machine["id"])
        assert changed["availability"][0] == [0, 120]
        assert changed["availability"][1][0] == 240
        assert changed["downtime"] == []
    finally:
        db.close()


def test_calendar_edit_changes_future_capacity_without_changing_saved_input():
    db, facility = seeded_session()
    try:
        before = build_snapshot(db, facility)
        run = ScheduleRun(
            facility_id=facility.id,
            algorithm="fcfs",
            objective="makespan",
            input_snapshot=before,
            status="queued",
            metrics={},
            diagnostics=[],
        )
        db.add(run)
        db.commit()
        calendar = db.get(OperatingCalendar, facility.operating_calendar_id)
        update_calendar(
            calendar.id,
            CalendarUpdate(weekly_shifts=[{"day": 0, "start": "08:00", "end": "16:00"}]),
            db,
        )
        after = build_snapshot(db, facility)
        assert after["machines"][0]["availability"][0] == [120, 600]
        assert db.get(ScheduleRun, run.id).input_snapshot["machines"][0]["availability"][0] == [0, 960]
        try:
            update_calendar(calendar.id, CalendarUpdate(timezone="Invalid/Zone"), db)
        except ApiError as exc:
            assert exc.status_code == 422
        else:
            raise AssertionError("Invalid calendar edit must return a client error")
        from pydantic import ValidationError

        try:
            CalendarCreate(name="Invalid", timezone="America/Chicago", holiday_exceptions=["not-a-date"])
        except ValidationError:
            pass
        else:
            raise AssertionError("Invalid holiday dates must be rejected")
    finally:
        db.close()


def test_csv_rejects_bad_duration_and_nonfinite_cost():
    db, facility = seeded_session()
    try:
        invalid_job = ImportRequest(
            facility_id=facility.id,
            category="jobs",
            csv_text=(
                "job_number,product_name,quantity,priority,release_time,due_time,operation_number,operation_name,machine_code,processing_minutes\n"
                "BAD,Bracket,1,2,2026-11-16T06:00:00-06:00,2026-11-16T18:00:00-06:00,10,Cut,M-01,-5\n"
            ),
        )
        result = _validate(db, invalid_job)
        assert any(item["field"] == "processing_minutes" for item in result["errors"])
        invalid_machine = ImportRequest(
            facility_id=facility.id,
            category="machines",
            csv_text="machine_code,machine_name,work_center_code,hourly_operating_cost\nM-X,New cell,CUT,inf\n",
        )
        result = _validate(db, invalid_machine)
        assert any(item["field"] == "hourly_operating_cost" for item in result["errors"])
        from pydantic import ValidationError

        try:
            MachineCreate(
                work_center_id="x", machine_code="M-X", machine_name="New cell", hourly_operating_cost="inf"
            )
        except ValidationError:
            pass
        else:
            raise AssertionError("Non-finite cost must be rejected")
    finally:
        db.close()


def test_standalone_operation_import_appends_machine_options_transactionally():
    db, facility = seeded_session()
    try:
        valid = ImportRequest(
            facility_id=facility.id,
            category="operations",
            csv_text=(
                "job_number,operation_number,operation_name,machine_code,processing_minutes,setup_group\n"
                "JOB-101,40,Secondary inspection,M-03,45,FINAL\n"
                "JOB-101,40,Secondary inspection,M-06,53,FINAL\n"
            ),
        )
        assert _validate(db, valid)["errors"] == []
        batch = confirm_import(valid, db)
        assert batch["import_status"] == "completed"
        db.expire_all()
        job = db.scalar(select(ProductionJob).where(ProductionJob.job_number == "JOB-101"))
        assert [operation.operation_number for operation in job.operations] == [10, 20, 30, 40]
        assert len(job.operations[-1].options) == 2

        mixed = ImportRequest(
            facility_id=facility.id,
            category="operations",
            csv_text=(
                "job_number,operation_number,operation_name,machine_code,processing_minutes\n"
                "JOB-101,50,Final audit,M-03,35\n"
                "JOB-102,40,Final audit,UNKNOWN,35\n"
            ),
        )
        rejected = confirm_import(mixed, db)
        assert rejected["import_status"] == "rejected"
        db.expire_all()
        assert [operation.operation_number for operation in job.operations] == [10, 20, 30, 40]
    finally:
        db.close()


def test_equal_time_simulation_events_keep_emitted_order():
    db, facility = seeded_session()
    try:
        snapshot = build_snapshot(db, facility)
        schedule = ScheduleRun(
            facility_id=facility.id,
            algorithm="fcfs",
            objective="makespan",
            input_snapshot=snapshot,
            status="completed",
            metrics={},
            diagnostics=[],
        )
        db.add(schedule)
        db.flush()
        simulation = SimulationRun(
            schedule_run_id=schedule.id,
            random_seed=42,
            configuration={"mode": "deterministic"},
            status="completed",
            summary_metrics={},
            diagnostics=[],
        )
        db.add(simulation)
        db.flush()
        db.add_all(
            [
                SimulationEvent(
                    simulation_run_id=simulation.id,
                    simulation_time=10,
                    sequence=1,
                    event_type="processing_started",
                    payload={},
                ),
                SimulationEvent(
                    simulation_run_id=simulation.id,
                    simulation_time=10,
                    sequence=0,
                    event_type="processing_completed",
                    payload={},
                ),
            ]
        )
        db.commit()
        db.expire_all()
        result = get_simulation_events(simulation.id, page=1, page_size=200, event_type=None, db=db)
        assert [item["sequence"] for item in result["items"]] == [0, 1]
        assert [item["event_type"] for item in result["items"]] == [
            "processing_completed",
            "processing_started",
        ]
    finally:
        db.close()


def test_late_worker_result_cannot_overwrite_cancelled_run(monkeypatch):
    db, facility = seeded_session()
    try:
        snapshot = build_snapshot(db, facility)
        run = ScheduleRun(
            facility_id=facility.id,
            algorithm="fcfs",
            objective="makespan",
            input_snapshot=snapshot,
            status="queued",
            metrics={},
            diagnostics=[],
        )
        db.add(run)
        db.commit()
        monkeypatch.setattr(run_worker, "SessionLocal", sessionmaker(bind=db.bind, expire_on_commit=False))

        class FakeProcess:
            exitcode = -15
            alive = True

            def is_alive(self):
                return self.alive

            def terminate(self):
                self.alive = False

            def kill(self):
                self.alive = False

            def join(self, timeout=None):  # noqa: ARG002
                pass

        process = FakeProcess()
        with run_worker._lock:
            run_worker._active[("schedule", run.id)] = (process, threading.Event())
        assert run_worker.cancel("schedule", run.id)
        from app.engineering import schedule

        late_result = schedule(snapshot, "fcfs")
        messages = queue.Queue()
        messages.put({"result": late_result})
        run_worker._monitor("schedule", run.id, process, messages)
        db.expire_all()
        persisted = db.get(ScheduleRun, run.id)
        assert persisted.status == "cancelled"
        assert persisted.assignments == []
    finally:
        db.close()
