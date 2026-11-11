"""Schedule, scenario, simulation, and calculated analysis routes."""

from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.http import ApiError, get_or_404, invalid
from app.engineering.utilization import compute_utilization_analysis
from app.models import Facility, Scenario, ScheduleRun, SimulationRun, new_id
from app.scenario_entities import clear_virtual_entities, materialize_virtual_entities
from app.schemas import ScenarioCreate, ScenarioEvaluate, ScenarioUpdate, ScheduleCreate, SimulationCreate
from app.serializers import (
    serialize_assignment,
    serialize_scenario,
    serialize_schedule,
    serialize_simulation,
    serialize_simulation_event,
)
from app.snapshot import apply_modifications, build_snapshot
from app.workers.runs import cancel, launch

router = APIRouter()


def _assignment_inputs(run: ScheduleRun) -> list[dict]:
    return [
        {
            "operation_id": item["operation_id"],
            "job_id": item["job_id"],
            "machine_id": item["machine_id"],
            "start_min": item["start_min"],
            "end_min": item["end_min"],
        }
        for item in (serialize_assignment(assignment, run.input_snapshot) for assignment in run.assignments)
    ]


def _create_schedule_run(
    db: Session,
    *,
    facility_id: str,
    algorithm: str,
    objective: str,
    name: str,
    snapshot: dict,
    scenario_id: str | None,
    time_limit_seconds: float,
    random_seed: int,
    search_workers: int,
) -> ScheduleRun:
    run = ScheduleRun(
        facility_id=facility_id,
        scenario_id=scenario_id,
        name=name or f"{algorithm.upper()} production schedule",
        algorithm=algorithm,
        objective=objective,
        input_snapshot=snapshot,
        solver_configuration={
            "time_limit_seconds": time_limit_seconds,
            "random_seed": random_seed,
            "search_workers": search_workers,
        },
        status="queued",
        diagnostics=[],
        metrics={},
    )
    db.add(run)
    db.commit()
    try:
        launch(
            "schedule",
            run.id,
            snapshot,
            {
                "algorithm": algorithm,
                "objective": objective,
                "time_limit_seconds": time_limit_seconds,
                "random_seed": random_seed,
                "search_workers": search_workers,
            },
        )
    except Exception as exc:
        run.status = "failed"
        run.diagnostics = [f"Could not start scheduling worker: {exc}"]
        db.commit()
        raise ApiError(503, "worker_unavailable", "Scheduling worker could not be started") from exc
    db.refresh(run)
    return run


@router.post("/schedules", status_code=202)
def create_schedule(body: ScheduleCreate, db: Session = Depends(get_db)) -> dict:
    facility = get_or_404(db, Facility, body.facility_id, "Facility")
    scenario = None
    if body.scenario_id:
        scenario = get_or_404(db, Scenario, body.scenario_id, "Scenario")
        if scenario.facility_id != facility.id:
            raise invalid("Scenario belongs to a different facility")
        if body.job_ids is not None:
            raise invalid("job_ids cannot be combined with a saved scenario snapshot")
        try:
            snapshot = apply_modifications(
                scenario.input_snapshot, scenario.modifications, scenario_id=scenario.id
            )
        except ValueError as exc:
            raise invalid(str(exc)) from exc
    else:
        try:
            snapshot = build_snapshot(db, facility, horizon_hours=body.horizon_hours, job_ids=body.job_ids)
        except ValueError as exc:
            raise invalid(str(exc)) from exc
    run = _create_schedule_run(
        db,
        facility_id=facility.id,
        algorithm=body.algorithm,
        objective=body.objective,
        name=body.name,
        snapshot=snapshot,
        scenario_id=scenario.id if scenario else None,
        time_limit_seconds=body.time_limit_seconds,
        random_seed=body.random_seed,
        search_workers=body.search_workers,
    )
    return serialize_schedule(run, include_assignments=False)


@router.get("/schedules")
def list_schedules(
    facility_id: str | None = None,
    scenario_id: str | None = None,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(ScheduleRun)
    if facility_id:
        stmt = stmt.where(ScheduleRun.facility_id == facility_id)
    if scenario_id:
        stmt = stmt.where(ScheduleRun.scenario_id == scenario_id)
    if status:
        stmt = stmt.where(ScheduleRun.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(ScheduleRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [serialize_schedule(item, include_assignments=False) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/schedules/{schedule_id}")
def get_schedule(schedule_id: str, db: Session = Depends(get_db)) -> dict:
    return serialize_schedule(get_or_404(db, ScheduleRun, schedule_id, "Schedule"))


@router.get("/schedules/{schedule_id}/operations")
def get_schedule_operations(schedule_id: str, db: Session = Depends(get_db)) -> dict:
    run = get_or_404(db, ScheduleRun, schedule_id, "Schedule")
    items = [serialize_assignment(item, run.input_snapshot) for item in run.assignments]
    return {"items": items, "total": len(items)}


@router.get("/schedules/{schedule_id}/metrics")
def get_schedule_metrics(schedule_id: str, db: Session = Depends(get_db)) -> dict:
    run = get_or_404(db, ScheduleRun, schedule_id, "Schedule")
    return {
        "schedule_id": run.id,
        "status": run.status,
        "solver_status": run.solver_status,
        "metrics": run.metrics,
    }


@router.post("/schedules/{schedule_id}/cancel")
def cancel_schedule(schedule_id: str, db: Session = Depends(get_db)) -> dict:
    get_or_404(db, ScheduleRun, schedule_id, "Schedule")
    if not cancel("schedule", schedule_id):
        raise ApiError(409, "not_running", "Only a queued or running schedule can be cancelled")
    db.expire_all()
    return serialize_schedule(get_or_404(db, ScheduleRun, schedule_id, "Schedule"), include_assignments=False)


def _scenario_base(db: Session, facility: Facility, baseline_schedule_id: str | None) -> dict:
    if baseline_schedule_id:
        baseline = get_or_404(db, ScheduleRun, baseline_schedule_id, "Baseline schedule")
        if baseline.facility_id != facility.id or baseline.status != "completed":
            raise invalid("Baseline must be a completed schedule from this facility")
        return deepcopy(baseline.input_snapshot)
    try:
        return build_snapshot(db, facility)
    except ValueError as exc:
        raise invalid(str(exc)) from exc


@router.post("/scenarios", status_code=201)
def create_scenario(body: ScenarioCreate, db: Session = Depends(get_db)) -> dict:
    facility = get_or_404(db, Facility, body.facility_id, "Facility")
    base = _scenario_base(db, facility, body.baseline_schedule_id)
    scenario = Scenario(
        id=new_id(),
        facility_id=facility.id,
        name=body.name,
        description=body.description,
        baseline_schedule_id=body.baseline_schedule_id,
        input_snapshot=base,
        modifications=body.modifications,
    )
    try:
        modified = apply_modifications(base, body.modifications, scenario_id=scenario.id)
        db.add(scenario)
        db.flush()
        materialize_virtual_entities(db, scenario, modified)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise invalid(str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(
            409, "conflict", "Scenario capacity or order identifier conflicts with existing data"
        ) from exc
    return serialize_scenario(scenario)


@router.get("/scenarios")
def list_scenarios(
    facility_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Scenario)
    if facility_id:
        stmt = stmt.where(Scenario.facility_id == facility_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(Scenario.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [serialize_scenario(item) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, db: Session = Depends(get_db)) -> dict:
    return serialize_scenario(get_or_404(db, Scenario, scenario_id, "Scenario"))


@router.patch("/scenarios/{scenario_id}")
def update_scenario(scenario_id: str, body: ScenarioUpdate, db: Session = Depends(get_db)) -> dict:
    scenario = get_or_404(db, Scenario, scenario_id, "Scenario")
    changes = body.model_dump(exclude_unset=True)
    if "modifications" in changes:
        used = db.scalar(
            select(func.count()).select_from(ScheduleRun).where(ScheduleRun.scenario_id == scenario.id)
        )
        if used:
            raise ApiError(
                409, "historical_reference", "Create a new scenario to change modifications after evaluation"
            )
        try:
            modified = apply_modifications(
                scenario.input_snapshot, changes["modifications"], scenario_id=scenario.id
            )
            clear_virtual_entities(db, scenario.id)
            scenario.modifications = changes["modifications"]
            materialize_virtual_entities(db, scenario, modified)
        except ValueError as exc:
            db.rollback()
            raise invalid(str(exc)) from exc
    for key in ("name", "description"):
        if key in changes:
            setattr(scenario, key, changes[key])
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "conflict", "Scenario identifier conflicts with existing data") from exc
    return serialize_scenario(scenario)


@router.post("/scenarios/{scenario_id}/evaluate", status_code=202)
def evaluate_scenario(scenario_id: str, body: ScenarioEvaluate, db: Session = Depends(get_db)) -> dict:
    scenario = get_or_404(db, Scenario, scenario_id, "Scenario")
    try:
        snapshot = apply_modifications(
            scenario.input_snapshot, scenario.modifications, scenario_id=scenario.id
        )
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    run = _create_schedule_run(
        db,
        facility_id=scenario.facility_id,
        algorithm=body.algorithm,
        objective=body.objective,
        name=f"{scenario.name} · {body.algorithm.upper()}",
        snapshot=snapshot,
        scenario_id=scenario.id,
        time_limit_seconds=body.time_limit_seconds,
        random_seed=body.random_seed,
        search_workers=body.search_workers,
    )
    return serialize_schedule(run, include_assignments=False)


@router.get("/scenarios/{scenario_id}/comparison")
def compare_scenario(scenario_id: str, db: Session = Depends(get_db)) -> dict:
    scenario = get_or_404(db, Scenario, scenario_id, "Scenario")
    if not scenario.baseline_schedule_id:
        raise invalid("Scenario has no baseline schedule")
    baseline = get_or_404(db, ScheduleRun, scenario.baseline_schedule_id, "Baseline schedule")
    candidate = db.scalar(
        select(ScheduleRun)
        .where(ScheduleRun.scenario_id == scenario.id, ScheduleRun.status == "completed")
        .order_by(ScheduleRun.completed_at.desc())
    )
    if candidate is None:
        raise invalid("Scenario has no completed evaluation")
    fields = [
        ("makespan_minutes", "min"),
        ("throughput_jobs_per_hour", "max"),
        ("average_machine_utilization", "context"),
        ("late_jobs", "min"),
        ("operating_cost", "min"),
        ("total_tardiness_minutes", "min"),
    ]
    rows = []
    for key, preference in fields:
        before = baseline.metrics.get(key)
        after = candidate.metrics.get(key)
        delta = (
            after - before if isinstance(before, (int, float)) and isinstance(after, (int, float)) else None
        )
        percent = delta / abs(before) * 100 if delta is not None and before else None
        rows.append(
            {
                "metric": key,
                "baseline": before,
                "scenario": after,
                "difference": delta,
                "difference_percent": percent,
                "preference": preference,
            }
        )
    return {
        "scenario_id": scenario.id,
        "baseline_schedule_id": baseline.id,
        "scenario_schedule_id": candidate.id,
        "rows": rows,
        "assumptions": scenario.modifications,
    }


@router.post("/simulations", status_code=202)
def create_simulation(body: SimulationCreate, db: Session = Depends(get_db)) -> dict:
    schedule_run = get_or_404(db, ScheduleRun, body.schedule_run_id, "Schedule")
    if schedule_run.status != "completed" or not schedule_run.assignments:
        raise invalid("Simulation requires a completed feasible schedule")
    config = body.model_dump(exclude={"schedule_run_id"})
    config["mode"] = "deterministic" if body.mode == "playback" else "stochastic"
    if body.mode == "playback":
        config["replications"] = 1
        config["processing_time_distribution"] = "fixed"
        config["processing_time_variability"] = 0
        config["failure_rate_per_hour"] = 0
    if config["horizon_minutes"] is None:
        config["horizon_minutes"] = schedule_run.input_snapshot["horizon_minutes"]
    run = SimulationRun(
        scenario_id=schedule_run.scenario_id,
        schedule_run_id=schedule_run.id,
        random_seed=body.random_seed,
        configuration=config,
        status="queued",
        summary_metrics={},
        diagnostics=[],
    )
    db.add(run)
    db.commit()
    try:
        launch(
            "simulation",
            run.id,
            schedule_run.input_snapshot,
            {"assignments": _assignment_inputs(schedule_run), "config": config},
        )
    except Exception as exc:
        run.status = "failed"
        run.diagnostics = [f"Could not start simulation worker: {exc}"]
        db.commit()
        raise ApiError(503, "worker_unavailable", "Simulation worker could not be started") from exc
    db.refresh(run)
    return serialize_simulation(run, schedule=schedule_run, include_events=False)


@router.get("/simulations")
def list_simulations(
    schedule_run_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(SimulationRun)
    if schedule_run_id:
        stmt = stmt.where(SimulationRun.schedule_run_id == schedule_run_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(SimulationRun.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [
            serialize_simulation(
                item,
                schedule=get_or_404(db, ScheduleRun, item.schedule_run_id, "Schedule"),
                include_events=False,
            )
            for item in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/simulations/{simulation_id}")
def get_simulation(simulation_id: str, db: Session = Depends(get_db)) -> dict:
    run = get_or_404(db, SimulationRun, simulation_id, "Simulation")
    schedule_run = get_or_404(db, ScheduleRun, run.schedule_run_id, "Schedule")
    return serialize_simulation(run, schedule=schedule_run)


@router.get("/simulations/{simulation_id}/events")
def get_simulation_events(
    simulation_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=1000),
    event_type: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    run = get_or_404(db, SimulationRun, simulation_id, "Simulation")
    schedule_run = get_or_404(db, ScheduleRun, run.schedule_run_id, "Schedule")
    from app.models import SimulationEvent

    stmt = select(SimulationEvent).where(SimulationEvent.simulation_run_id == run.id)
    if event_type:
        stmt = stmt.where(SimulationEvent.event_type == event_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(SimulationEvent.sequence).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [serialize_simulation_event(item, schedule_run.input_snapshot["origin"]) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/simulations/{simulation_id}/metrics")
def get_simulation_metrics(simulation_id: str, db: Session = Depends(get_db)) -> dict:
    run = get_or_404(db, SimulationRun, simulation_id, "Simulation")
    return {"simulation_id": run.id, "status": run.status, "metrics": run.summary_metrics}


@router.post("/simulations/{simulation_id}/cancel")
def cancel_simulation(simulation_id: str, db: Session = Depends(get_db)) -> dict:
    run = get_or_404(db, SimulationRun, simulation_id, "Simulation")
    if not cancel("simulation", simulation_id):
        raise ApiError(409, "not_running", "Only a queued or running simulation can be cancelled")
    db.expire_all()
    fresh = get_or_404(db, SimulationRun, simulation_id, "Simulation")
    schedule_run = get_or_404(db, ScheduleRun, run.schedule_run_id, "Schedule")
    return serialize_simulation(fresh, schedule=schedule_run, include_events=False)


def _completed_schedule(db: Session, schedule_id: str) -> ScheduleRun:
    run = get_or_404(db, ScheduleRun, schedule_id, "Schedule")
    if run.status != "completed":
        raise invalid("Analysis requires a completed feasible schedule")
    return run


@router.get("/analytics/utilization")
def utilization(
    schedule_id: str,
    start_min: int = Query(default=0, ge=0, le=525_600),
    end_min: int | None = Query(default=None, ge=1, le=525_600),
    db: Session = Depends(get_db),
) -> dict:
    run = _completed_schedule(db, schedule_id)
    horizon = run.input_snapshot["horizon_minutes"]
    final_end = end_min if end_min is not None else run.metrics.get("observation_end_min", horizon)
    if not isinstance(final_end, int) or not (0 <= start_min < final_end <= horizon):
        raise invalid("Utilization range must have a positive duration within the saved scheduling horizon")
    analysis = compute_utilization_analysis(run.input_snapshot, _assignment_inputs(run), start_min, final_end)
    return {
        "schedule_id": run.id,
        **analysis,
        "definition": "Busy production minutes divided by available operating-calendar minutes after planned downtime, over the selected half-open interval.",
    }


@router.get("/analytics/throughput")
def throughput(schedule_id: str, db: Session = Depends(get_db)) -> dict:
    run = _completed_schedule(db, schedule_id)
    return {
        "schedule_id": run.id,
        "completed_jobs": run.metrics.get("completed_jobs"),
        "throughput_jobs_per_hour": run.metrics.get("throughput_jobs_per_hour"),
        "makespan_minutes": run.metrics.get("makespan_minutes"),
        "job_metrics": run.metrics.get("job_metrics", []),
        "definition": "Completed jobs divided by scheduled elapsed hours from the schedule origin through final completion.",
    }


@router.get("/analytics/bottlenecks")
def bottlenecks(
    schedule_id: str,
    simulation_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    from app.engineering import analyze_bottlenecks

    run = _completed_schedule(db, schedule_id)
    simulation_metrics = None
    if simulation_id is None:
        latest = db.scalar(
            select(SimulationRun)
            .where(SimulationRun.schedule_run_id == run.id, SimulationRun.status == "completed")
            .order_by(SimulationRun.completed_at.desc())
        )
        if latest is not None:
            simulation_id = latest.id
    if simulation_id:
        simulation = get_or_404(db, SimulationRun, simulation_id, "Simulation")
        if simulation.schedule_run_id != run.id or simulation.status != "completed":
            raise invalid("Simulation must be completed for the selected schedule")
        simulation_metrics = simulation.summary_metrics
    result = analyze_bottlenecks(run.input_snapshot, _assignment_inputs(run), simulation_metrics)
    return {"schedule_id": run.id, "simulation_id": simulation_id, **result}
