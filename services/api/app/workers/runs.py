"""Cancelable isolated processes for schedules and simulations.

The API runs one local worker process per calculation. This keeps request
handlers responsive and lets cancellation stop the actual solver/simulation.
For a multi-instance deployment, replace this local registry with a durable
queue and shared worker ownership.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import queue
import threading
from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import ScheduledOperation, ScheduleRun, SimulationEvent, SimulationRun, utc_now
from app.snapshot import parse_aware

logger = logging.getLogger(__name__)
_context = mp.get_context("spawn")
_lock = threading.Lock()
_active: dict[tuple[str, str], tuple[mp.Process, object]] = {}


def _worker_entry(kind: str, snapshot: dict, payload: dict, output, cancel_event) -> None:
    try:
        if kind == "schedule":
            from app.engineering import schedule

            result = schedule(
                snapshot,
                algorithm=payload["algorithm"],
                objective=payload["objective"],
                time_limit_seconds=payload["time_limit_seconds"],
                random_seed=payload["random_seed"],
                cancel_event=cancel_event,
                search_workers=payload["search_workers"],
            )
        else:
            from app.engineering import simulate

            result = simulate(snapshot, payload["assignments"], payload["config"])
        output.put({"result": result})
    except Exception as exc:  # failure must be persisted for the user
        logger.exception("Engineering worker failed")
        output.put({"error": f"{type(exc).__name__}: {exc}"})


def _save_schedule(db: Session, run: ScheduleRun, result: dict) -> None:
    from app.engineering import validate_schedule

    run.solver_status = result.get("solver_status") or result.get("status")
    runtime_ms = result.get("runtime_ms")
    run.duration_ms = round(runtime_ms) if isinstance(runtime_ms, (int, float)) else None
    run.objective_value = result.get("objective_value")
    run.diagnostics = [str(item) for item in result.get("diagnostics", [])]
    run.completed_at = utc_now()
    outcome = result.get("status")
    if outcome == "feasible" and run.algorithm != "cp_sat" and run.solver_status == "DISPATCHED":
        run.solver_status = "FEASIBLE"
    if outcome == "cancelled":
        run.status = "cancelled"
        return
    if outcome not in {"optimal", "feasible"}:
        run.status = "failed"
        if not run.diagnostics:
            run.diagnostics = [f"Solver returned {outcome or 'unknown'} without a feasible schedule"]
        return
    assignments = result.get("assignments", [])
    validation = validate_schedule(run.input_snapshot, assignments)
    if not validation.get("valid"):
        run.status = "failed"
        run.solver_status = "error"
        run.diagnostics = ["Generated schedule failed independent validation"] + [
            str(item) for item in validation.get("errors", [])
        ]
        return
    origin = parse_aware(run.input_snapshot["origin"])
    positions: dict[str, int] = {}
    for assignment in sorted(assignments, key=lambda item: (item["machine_id"], item["start_min"])):
        machine_id = assignment["machine_id"]
        positions[machine_id] = positions.get(machine_id, 0) + 1
        db.add(
            ScheduledOperation(
                schedule_run_id=run.id,
                operation_id=assignment["operation_id"],
                machine_id=machine_id,
                start_time=origin + timedelta(minutes=assignment["start_min"]),
                end_time=origin + timedelta(minutes=assignment["end_min"]),
                sequence_position=positions[machine_id],
            )
        )
    run.metrics = result.get("metrics") or {}
    run.status = "completed"


def _save_simulation(db: Session, run: SimulationRun, result: dict) -> None:
    run.completed_at = utc_now()
    run.diagnostics = [str(item) for item in result.get("diagnostics", [])]
    if result.get("status") != "completed":
        run.status = "failed"
        if not run.diagnostics:
            run.diagnostics = ["Simulation did not complete"]
        return
    run.summary_metrics = {
        **(result.get("metrics") or {}),
        "replication_metrics": result.get("replication_metrics") or [],
        "confidence_intervals": result.get("confidence_intervals") or {},
    }
    for sequence, event in enumerate(result.get("events") or []):
        db.add(
            SimulationEvent(
                simulation_run_id=run.id,
                simulation_time=float(event["time_min"]),
                sequence=sequence,
                event_type=event["event_type"],
                job_id=event.get("job_id"),
                operation_id=event.get("operation_id"),
                machine_id=event.get("machine_id"),
                payload=event.get("payload") or {},
            )
        )
    run.status = "completed"


def _monitor(kind: str, run_id: str, process: mp.Process, output) -> None:
    message = None
    while True:
        try:
            message = output.get(timeout=0.25)
            break
        except queue.Empty:
            if not process.is_alive():
                try:
                    message = output.get(timeout=0.5)
                except queue.Empty:
                    pass
                break
    process.join(timeout=2)
    if hasattr(output, "close"):
        output.close()
    # The lock covers status check through commit. A cancel request either wins
    # before persistence or sees a terminal run, never an overwritten cancel.
    with _lock:
        try:
            with SessionLocal() as db:
                model = ScheduleRun if kind == "schedule" else SimulationRun
                run = db.get(model, run_id)
                if run is None:
                    return
                db.refresh(run)
                if run.status == "cancelled":
                    return
                if message is None:
                    run.status = "failed"
                    run.diagnostics = [f"Worker exited without a result (exit code {process.exitcode})"]
                    run.completed_at = utc_now()
                elif "error" in message:
                    run.status = "failed"
                    run.diagnostics = [message["error"]]
                    run.completed_at = utc_now()
                elif kind == "schedule":
                    _save_schedule(db, run, message["result"])
                else:
                    _save_simulation(db, run, message["result"])
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.exception("Failed to persist %s run %s", kind, run_id)
                    # A second transaction leaves a visible terminal error state.
                    fresh = db.get(model, run_id)
                    if fresh is not None and fresh.status != "cancelled":
                        fresh.status = "failed"
                        fresh.diagnostics = ["Run finished but its result could not be saved"]
                        fresh.completed_at = utc_now()
                        db.commit()
        finally:
            _active.pop((kind, run_id), None)


def launch(kind: str, run_id: str, snapshot: dict, payload: dict) -> None:
    if kind not in {"schedule", "simulation"}:
        raise ValueError("Unsupported run kind")
    output = _context.Queue()
    cancel_event = _context.Event()
    process = _context.Process(
        target=_worker_entry,
        args=(kind, snapshot, payload, output, cancel_event),
        daemon=True,
        name=f"linepilot-{kind}-{run_id[:8]}",
    )
    with _lock:
        _active[(kind, run_id)] = (process, cancel_event)
        try:
            process.start()
            with SessionLocal() as db:
                model = ScheduleRun if kind == "schedule" else SimulationRun
                run = db.get(model, run_id)
                if run and run.status == "queued":
                    run.status = "running"
                    run.started_at = utc_now()
                    db.commit()
        except Exception:
            _active.pop((kind, run_id), None)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            output.close()
            raise
    threading.Thread(target=_monitor, args=(kind, run_id, process, output), daemon=True).start()


def cancel(kind: str, run_id: str) -> bool:
    model = ScheduleRun if kind == "schedule" else SimulationRun
    with _lock:
        with SessionLocal() as db:
            run = db.get(model, run_id)
            if run is None or run.status not in {"queued", "running"}:
                return False
            run.status = "cancelled"
            run.completed_at = utc_now()
            run.diagnostics = list(run.diagnostics or []) + ["Cancelled by user"]
            db.commit()
        active = _active.get((kind, run_id))
    if active:
        process, event = active
        event.set()
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
    return True


def recover_interrupted_runs() -> None:
    """Mark orphaned local workers after an API restart."""
    from sqlalchemy import select

    with SessionLocal() as db:
        for model in (ScheduleRun, SimulationRun):
            for run in db.scalars(select(model).where(model.status.in_(["queued", "running"]))):
                run.status = "failed"
                run.completed_at = utc_now()
                run.diagnostics = list(run.diagnostics or []) + ["API restarted before the run completed"]
        db.commit()
