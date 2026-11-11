"""Database-fenced execution for at-least-once queue deliveries.

The queue contains only a run kind and ID. The immutable input and settings
remain in PostgreSQL. A lease token prevents a late or duplicate subscriber
from overwriting a newer attempt or a user's cancellation.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import and_, func, or_, select, update

from app.core.db import SessionLocal
from app.models import ScheduleRun, SimulationRun, new_id, utc_now
from app.serializers import serialize_assignment
from app.workers.runs import _save_schedule, _save_simulation

logger = logging.getLogger(__name__)
RunKind = Literal["schedule", "simulation"]
ClaimOutcome = Literal["ack", "retry"]
QUEUE_BACKEND = "vercel_queue"
LEASE_SECONDS = 60
LEASE_RENEW_SECONDS = 10
CANCEL_POLL_SECONDS = 0.5
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class ClaimedRun:
    kind: RunKind
    run_id: str
    token: str
    snapshot: dict[str, Any]
    payload: dict[str, Any]


def run_backend() -> str:
    value = os.getenv("LINEPILOT_RUN_BACKEND", "local").strip().lower()
    if value not in {"local", QUEUE_BACKEND}:
        raise RuntimeError("LINEPILOT_RUN_BACKEND must be local or vercel_queue")
    if os.getenv("VERCEL") == "1" and value != QUEUE_BACKEND:
        raise RuntimeError("LINEPILOT_RUN_BACKEND=vercel_queue is required on Vercel")
    return value


def _model(kind: RunKind):
    if kind == "schedule":
        return ScheduleRun
    if kind == "simulation":
        return SimulationRun
    raise ValueError(f"Unknown run kind {kind}")


def _send_message(kind: RunKind, run_id: str) -> None:
    from vercel.queue.sync import send

    from app.queue_messages import RUN_TOPIC, RunMessage

    # Reconciliation must create a fresh delivery if an earlier queue message
    # expires or exhausts its retries. The database claim fences duplicates.
    send(RUN_TOPIC, RunMessage(kind=kind, run_id=run_id))


def publish_run(kind: RunKind, run_id: str) -> None:
    """Publish after the run row commits; republishing is safe by run ID."""
    _send_message(kind, run_id)
    model = _model(kind)
    try:
        with SessionLocal.begin() as db:
            db.execute(
                update(model)
                .where(
                    model.id == run_id,
                    model.execution_backend == QUEUE_BACKEND,
                    model.status.in_(("queued", "running")),
                )
                .values(last_enqueued_at=utc_now())
            )
    except Exception:
        # The accepted queue message is durable. This timestamp is only a
        # reconciliation throttle and must not turn a successful send into 503.
        logger.exception("Could not record queue publish time for %s %s", kind, run_id)


def fail_queued_submission(kind: RunKind, run_id: str) -> None:
    """Leave a visible terminal result when the API cannot enqueue a run."""
    model = _model(kind)
    with SessionLocal.begin() as db:
        db.execute(
            update(model)
            .where(
                model.id == run_id,
                model.execution_backend == QUEUE_BACKEND,
                model.status == "queued",
            )
            .values(
                status="failed",
                completed_at=utc_now(),
                diagnostics=["Run service could not accept this request. Please retry."],
            )
        )


def cancel_queue_run(kind: RunKind, run_id: str) -> bool:
    """Persist a cancellation tombstone that every subscriber must respect."""
    model = _model(kind)
    with SessionLocal.begin() as db:
        changed = db.execute(
            update(model)
            .where(
                model.id == run_id,
                model.execution_backend == QUEUE_BACKEND,
                model.status.in_(("queued", "running")),
            )
            .values(
                status="cancelled",
                completed_at=utc_now(),
                claim_token=None,
                lease_expires_at=None,
            )
        )
        if changed.rowcount != 1:
            return False
        run = db.get(model, run_id)
        run.diagnostics = list(run.diagnostics or []) + ["Cancelled by user"]
    return True


def _assignment_inputs(run: ScheduleRun) -> list[dict[str, Any]]:
    rows = []
    for assignment in run.assignments:
        item = serialize_assignment(assignment, run.input_snapshot)
        rows.append(
            {
                "operation_id": item["operation_id"],
                "job_id": item["job_id"],
                "machine_id": item["machine_id"],
                "start_min": item["start_min"],
                "end_min": item["end_min"],
            }
        )
    return rows


def _claimed_payload(db, kind: RunKind, run, token: str) -> ClaimedRun:
    if kind == "schedule":
        config = run.solver_configuration or {}
        return ClaimedRun(
            kind,
            run.id,
            token,
            run.input_snapshot,
            {
                "algorithm": run.algorithm,
                "objective": run.objective,
                "time_limit_seconds": config.get("time_limit_seconds", 20),
                "random_seed": config.get("random_seed", 42),
                "search_workers": config.get("search_workers", 1),
            },
        )
    source = db.get(ScheduleRun, run.schedule_run_id)
    if source is None:
        raise ValueError(f"Source schedule is missing for simulation {run.id}")
    return ClaimedRun(
        kind,
        run.id,
        token,
        source.input_snapshot,
        {"assignments": _assignment_inputs(source), "config": run.configuration},
    )


def _claim(kind: RunKind, run_id: str) -> ClaimedRun | ClaimOutcome:
    model = _model(kind)
    now = utc_now()
    token = new_id()
    claimable = or_(
        model.status == "queued",
        and_(
            model.status == "running",
            or_(model.lease_expires_at.is_(None), model.lease_expires_at <= now),
        ),
    )
    with SessionLocal.begin() as db:
        claimed = db.execute(
            update(model)
            .where(
                model.id == run_id,
                model.execution_backend == QUEUE_BACKEND,
                model.attempt_count < MAX_ATTEMPTS,
                claimable,
            )
            .values(
                status="running",
                claim_token=token,
                lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                attempt_count=model.attempt_count + 1,
                started_at=func.coalesce(model.started_at, now),
            )
        )
        if claimed.rowcount == 1:
            run = db.get(model, run_id)
            try:
                return _claimed_payload(db, kind, run, token)
            except ValueError as exc:
                run.status = "failed"
                run.completed_at = now
                run.claim_token = None
                run.lease_expires_at = None
                run.diagnostics = [str(exc)]
                return "ack"

        run = db.get(model, run_id)
        if (
            run is None
            or run.execution_backend != QUEUE_BACKEND
            or run.status
            in {
                "completed",
                "failed",
                "cancelled",
            }
        ):
            return "ack"
        if run.attempt_count >= MAX_ATTEMPTS:
            exhausted = db.execute(
                update(model)
                .where(
                    model.id == run_id,
                    model.execution_backend == QUEUE_BACKEND,
                    model.attempt_count >= MAX_ATTEMPTS,
                    claimable,
                )
                .values(
                    status="failed",
                    completed_at=now,
                    claim_token=None,
                    lease_expires_at=None,
                    diagnostics=["The run failed after repeated worker attempts."],
                )
            )
            if exhausted.rowcount == 1:
                return "ack"
        # Never acknowledge a duplicate delivery while another attempt is
        # still running: that could remove its only retry after a crash.
        return "retry"


def _watch_claim(claim: ClaimedRun, cancel_event: threading.Event, stop: threading.Event) -> None:
    model = _model(claim.kind)
    next_renewal = time.monotonic() + LEASE_RENEW_SECONDS
    while not stop.is_set():
        try:
            with SessionLocal.begin() as db:
                row = db.execute(
                    select(model.status, model.claim_token).where(model.id == claim.run_id)
                ).one_or_none()
                if row is None or row.status != "running" or row.claim_token != claim.token:
                    cancel_event.set()
                    return
                if time.monotonic() >= next_renewal:
                    renewed = db.execute(
                        update(model)
                        .where(
                            model.id == claim.run_id,
                            model.status == "running",
                            model.claim_token == claim.token,
                        )
                        .values(lease_expires_at=utc_now() + timedelta(seconds=LEASE_SECONDS))
                    )
                    if renewed.rowcount != 1:
                        cancel_event.set()
                        return
                    next_renewal = time.monotonic() + LEASE_RENEW_SECONDS
        except Exception:
            logger.exception("Lost queue run lease for %s %s", claim.kind, claim.run_id)
            cancel_event.set()
            return
        stop.wait(CANCEL_POLL_SECONDS)


def _finish(claim: ClaimedRun, result: dict[str, Any]) -> ClaimOutcome:
    model = _model(claim.kind)
    with SessionLocal.begin() as db:
        # The conditional write locks this row through result persistence. A
        # cancellation or newer attempt wins only before this transaction.
        fenced = db.execute(
            update(model)
            .where(
                model.id == claim.run_id,
                model.execution_backend == QUEUE_BACKEND,
                model.status == "running",
                model.claim_token == claim.token,
            )
            .values(lease_expires_at=utc_now() + timedelta(seconds=LEASE_SECONDS))
        )
        if fenced.rowcount != 1:
            run = db.get(model, claim.run_id)
            return "ack" if run is None or run.status in {"completed", "failed", "cancelled"} else "retry"
        run = db.get(model, claim.run_id)
        if result.get("status") == "cancelled":
            # The monitor may have stopped on a transient DB error. Only the
            # API's persisted cancellation may make the run terminal.
            run.lease_expires_at = utc_now()
            return "retry"
        if claim.kind == "schedule":
            _save_schedule(db, run, result)
        else:
            _save_simulation(db, run, result)
        run.claim_token = None
        run.lease_expires_at = None
    return "ack"


def _release_for_retry(claim: ClaimedRun) -> None:
    model = _model(claim.kind)
    try:
        with SessionLocal.begin() as db:
            db.execute(
                update(model)
                .where(
                    model.id == claim.run_id,
                    model.status == "running",
                    model.claim_token == claim.token,
                )
                .values(
                    lease_expires_at=utc_now(),
                    diagnostics=["A worker attempt failed; the run will be retried."],
                )
            )
    except Exception:
        logger.exception("Could not release failed queue claim for %s %s", claim.kind, claim.run_id)


def process_queued_run(kind: RunKind, run_id: str) -> ClaimOutcome:
    """Process a delivery once; the queue retries when this raises or returns retry."""
    from app.engineering import schedule, simulate

    claim = _claim(kind, run_id)
    if not isinstance(claim, ClaimedRun):
        return claim
    cancel_event = threading.Event()
    stop = threading.Event()
    watcher = threading.Thread(target=_watch_claim, args=(claim, cancel_event, stop), daemon=True)
    watcher.start()
    try:
        try:
            if kind == "schedule":
                result = schedule(claim.snapshot, cancel_event=cancel_event, **claim.payload)
            else:
                result = simulate(
                    claim.snapshot,
                    claim.payload["assignments"],
                    claim.payload["config"],
                    cancel_event=cancel_event,
                )
        finally:
            stop.set()
            watcher.join(timeout=1)
        return _finish(claim, result)
    except Exception:
        logger.exception("Queue run attempt failed for %s %s", kind, run_id)
        _release_for_retry(claim)
        raise


def _reconcile_candidate(model, now: datetime):
    queued_before = now - timedelta(seconds=30)
    resend_before = now - timedelta(minutes=5)
    return and_(
        model.execution_backend == QUEUE_BACKEND,
        or_(
            and_(model.status == "queued", model.created_at <= queued_before),
            and_(
                model.status == "running",
                or_(model.lease_expires_at.is_(None), model.lease_expires_at <= now),
            ),
        ),
        or_(model.last_enqueued_at.is_(None), model.last_enqueued_at <= resend_before),
    )


def _reserve_republish(kind: RunKind, run_id: str, now: datetime) -> bool:
    """Reserve one stale row across overlapping scheduled invocations."""
    model = _model(kind)
    with SessionLocal.begin() as db:
        changed = db.execute(
            update(model)
            .where(model.id == run_id, _reconcile_candidate(model, now))
            .values(last_enqueued_at=now)
        )
        return changed.rowcount == 1


def _release_republish(kind: RunKind, run_id: str, reserved_at: datetime) -> None:
    model = _model(kind)
    try:
        with SessionLocal.begin() as db:
            db.execute(
                update(model)
                .where(model.id == run_id, model.last_enqueued_at == reserved_at)
                .values(last_enqueued_at=None)
            )
    except Exception:
        logger.exception("Could not release failed republish for %s %s", kind, run_id)


def reconcile_queue_runs(limit: int = 20) -> dict[str, int]:
    """Republish abandoned DB rows after the commit-to-send crash window."""
    if run_backend() != QUEUE_BACKEND:
        return {"attempted": 0, "published": 0, "failed": 0}
    now = utc_now()
    candidates: list[tuple[RunKind, str]] = []
    with SessionLocal() as db:
        for kind in ("schedule", "simulation"):
            model = _model(kind)
            remaining = max(0, limit - len(candidates))
            if not remaining:
                break
            ids = db.scalars(
                select(model.id)
                .where(_reconcile_candidate(model, now))
                .order_by(model.created_at)
                .limit(remaining)
            ).all()
            candidates.extend((kind, run_id) for run_id in ids)
    attempted = 0
    published = 0
    for kind, run_id in candidates:
        if not _reserve_republish(kind, run_id, now):
            continue
        attempted += 1
        try:
            # The reservation doubles as the send timestamp. A fresh message
            # is safe because the consumer fences every result in PostgreSQL.
            _send_message(kind, run_id)
            published += 1
        except Exception:
            logger.exception("Could not republish queue run %s %s", kind, run_id)
            _release_republish(kind, run_id, now)
    return {"attempted": attempted, "published": published, "failed": attempted - published}
