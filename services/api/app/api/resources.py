"""Facility configuration and production data management endpoints."""

from __future__ import annotations

from datetime import timezone

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.http import ApiError, get_or_404, invalid
from app.models import (
    AuditEvent,
    DowntimeEvent,
    Facility,
    Machine,
    OperatingCalendar,
    OperationMachineOption,
    ProductionJob,
    ProductionOperation,
    Scenario,
    ScheduledOperation,
    ScheduleRun,
    WorkCenter,
    Workspace,
)
from app.schemas import (
    CalendarCreate,
    CalendarUpdate,
    DowntimeCreate,
    FacilityCreate,
    JobCreate,
    JobUpdate,
    MachineCreate,
    MachineUpdate,
    WorkCenterCreate,
    WorkspaceCreate,
)
from app.serializers import (
    serialize_calendar,
    serialize_downtime,
    serialize_facility,
    serialize_job,
    serialize_machine,
    serialize_scenario,
    serialize_schedule,
    serialize_work_center,
    serialize_workspace,
)

router = APIRouter()


def _audit(
    db: Session,
    facility: Facility,
    entity_type: str,
    entity_id: str,
    event_type: str,
    details: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            workspace_id=facility.workspace_id,
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            details=details or {},
        )
    )


def _commit(db: Session, conflict_message: str = "A record with this identifier already exists") -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, "conflict", conflict_message) from exc


def _facility_machines(db: Session, facility_id: str) -> list[Machine]:
    return db.scalars(
        select(Machine)
        .join(WorkCenter)
        .where(WorkCenter.facility_id == facility_id, Machine.scenario_id.is_(None))
        .options(selectinload(Machine.downtime))
        .order_by(Machine.machine_code)
    ).all()


def _job_query(facility_id: str | None = None):
    stmt = (
        select(ProductionJob)
        .where(ProductionJob.scenario_id.is_(None))
        .options(selectinload(ProductionJob.operations).selectinload(ProductionOperation.options))
    )
    if facility_id:
        stmt = stmt.where(ProductionJob.facility_id == facility_id)
    return stmt


@router.get("/bootstrap")
def bootstrap(facility_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    if facility_id:
        facility = get_or_404(db, Facility, facility_id, "Facility")
    else:
        facility = db.scalar(select(Facility).where(Facility.facility_code == "NORTHLINE"))
        if facility is None:
            facility = db.scalar(select(Facility).order_by(Facility.created_at))
    if facility is None:
        raise ApiError(
            503, "not_initialized", "No facility exists. Run the seed command or create a facility."
        )
    workspace = get_or_404(db, Workspace, facility.workspace_id, "Workspace")
    centers = db.scalars(
        select(WorkCenter).where(WorkCenter.facility_id == facility.id).order_by(WorkCenter.display_order)
    ).all()
    machines = _facility_machines(db, facility.id)
    jobs = db.scalars(_job_query(facility.id).order_by(ProductionJob.job_number)).all()
    runs = db.scalars(
        select(ScheduleRun)
        .where(ScheduleRun.facility_id == facility.id)
        .order_by(ScheduleRun.created_at.desc())
        .limit(20)
    ).all()
    scenarios = db.scalars(
        select(Scenario)
        .where(Scenario.facility_id == facility.id)
        .order_by(Scenario.created_at.desc())
        .limit(20)
    ).all()
    latest = next((run for run in runs if run.status == "completed"), None)
    return {
        "workspace": serialize_workspace(workspace),
        "facility": serialize_facility(facility),
        "work_centers": [serialize_work_center(item) for item in centers],
        "machines": [serialize_machine(item) for item in machines],
        "jobs": [serialize_job(item) for item in jobs],
        "schedules": [serialize_schedule(item, include_assignments=False) for item in runs],
        "scenarios": [serialize_scenario(item) for item in scenarios],
        "latest_schedule": serialize_schedule(latest) if latest else None,
        "sample_data_notice": (
            "Fictional Northline sample data. Results are calculated from this dataset."
            if facility.sample_data
            else None
        ),
    }


@router.get("/workspaces")
def list_workspaces(db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(Workspace).order_by(Workspace.name)).all()
    return {"items": [serialize_workspace(item) for item in items], "total": len(items)}


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceCreate, db: Session = Depends(get_db)) -> dict:
    workspace = Workspace(**body.model_dump())
    db.add(workspace)
    _commit(db)
    return serialize_workspace(workspace)


@router.get("/facilities")
def list_facilities(db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(Facility).order_by(Facility.facility_name)).all()
    return {"items": [serialize_facility(item) for item in items], "total": len(items)}


@router.post("/facilities", status_code=201)
def create_facility(body: FacilityCreate, db: Session = Depends(get_db)) -> dict:
    get_or_404(db, Workspace, body.workspace_id, "Workspace")
    if body.operating_calendar_id:
        get_or_404(db, OperatingCalendar, body.operating_calendar_id, "Calendar")
    facility = Facility(**body.model_dump(), sample_data=False, status="active")
    db.add(facility)
    _commit(db, "Facility code already exists in this workspace")
    return serialize_facility(facility)


@router.get("/facilities/{facility_id}")
def get_facility(facility_id: str, db: Session = Depends(get_db)) -> dict:
    return serialize_facility(get_or_404(db, Facility, facility_id, "Facility"))


@router.get("/work-centers")
def list_work_centers(facility_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    stmt = select(WorkCenter)
    if facility_id:
        stmt = stmt.where(WorkCenter.facility_id == facility_id)
    items = db.scalars(stmt.order_by(WorkCenter.display_order, WorkCenter.code)).all()
    return {"items": [serialize_work_center(item) for item in items], "total": len(items)}


@router.post("/work-centers", status_code=201)
def create_work_center(body: WorkCenterCreate, db: Session = Depends(get_db)) -> dict:
    facility = get_or_404(db, Facility, body.facility_id, "Facility")
    center = WorkCenter(**body.model_dump())
    db.add(center)
    db.flush()
    _audit(db, facility, "work_center", center.id, "created")
    _commit(db, "Work center code already exists in this facility")
    return serialize_work_center(center)


@router.get("/calendars")
def list_calendars(db: Session = Depends(get_db)) -> dict:
    items = db.scalars(select(OperatingCalendar).order_by(OperatingCalendar.name)).all()
    return {"items": [serialize_calendar(item) for item in items], "total": len(items)}


@router.post("/calendars", status_code=201)
def create_calendar(body: CalendarCreate, db: Session = Depends(get_db)) -> dict:
    calendar = OperatingCalendar(**body.model_dump())
    db.add(calendar)
    _commit(db)
    return serialize_calendar(calendar)


@router.get("/calendars/{calendar_id}")
def get_calendar(calendar_id: str, db: Session = Depends(get_db)) -> dict:
    return serialize_calendar(get_or_404(db, OperatingCalendar, calendar_id, "Calendar"))


@router.patch("/calendars/{calendar_id}")
def update_calendar(calendar_id: str, body: CalendarUpdate, db: Session = Depends(get_db)) -> dict:
    calendar = get_or_404(db, OperatingCalendar, calendar_id, "Calendar")
    merged = {
        "name": calendar.name,
        "timezone": calendar.timezone,
        "weekly_shifts": calendar.weekly_shifts,
        "holiday_exceptions": calendar.holiday_exceptions,
        "special_closures": calendar.special_closures,
        **body.model_dump(exclude_unset=True),
    }
    try:
        validated = CalendarCreate.model_validate(merged)
    except ValidationError as exc:
        raise invalid(
            "Calendar validation failed",
            [
                {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
                for item in exc.errors()
            ],
        ) from exc
    for key, value in validated.model_dump().items():
        setattr(calendar, key, value)
    _commit(db)
    return serialize_calendar(calendar)


@router.get("/machines")
def list_machines(
    facility_id: str | None = None,
    work_center_id: str | None = None,
    active: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    stmt = (
        select(Machine)
        .join(WorkCenter)
        .where(Machine.scenario_id.is_(None))
        .options(selectinload(Machine.downtime))
    )
    if facility_id:
        stmt = stmt.where(WorkCenter.facility_id == facility_id)
    if work_center_id:
        stmt = stmt.where(Machine.work_center_id == work_center_id)
    if active is not None:
        stmt = stmt.where(Machine.active == active)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(Machine.machine_code).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [serialize_machine(item) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/machines", status_code=201)
def create_machine(body: MachineCreate, db: Session = Depends(get_db)) -> dict:
    center = get_or_404(db, WorkCenter, body.work_center_id, "Work center")
    if body.operating_calendar_id:
        get_or_404(db, OperatingCalendar, body.operating_calendar_id, "Calendar")
    machine = Machine(**body.model_dump())
    db.add(machine)
    db.flush()
    _audit(db, center.facility, "machine", machine.id, "created")
    _commit(db, "Machine code already exists")
    return serialize_machine(machine)


@router.get("/machines/{machine_id}")
def get_machine(machine_id: str, db: Session = Depends(get_db)) -> dict:
    return serialize_machine(get_or_404(db, Machine, machine_id, "Machine"))


@router.patch("/machines/{machine_id}")
def update_machine(machine_id: str, body: MachineUpdate, db: Session = Depends(get_db)) -> dict:
    machine = get_or_404(db, Machine, machine_id, "Machine")
    if machine.scenario_id:
        raise ApiError(
            409, "scenario_resource", "Scenario-only capacity cannot be edited as a facility machine"
        )
    changes = body.model_dump(exclude_unset=True)
    if changes.get("operating_calendar_id"):
        get_or_404(db, OperatingCalendar, changes["operating_calendar_id"], "Calendar")
    for key, value in changes.items():
        setattr(machine, key, value)
    _audit(db, machine.work_center.facility, "machine", machine.id, "updated", {"fields": list(changes)})
    _commit(db)
    return serialize_machine(machine)


@router.post("/downtime", status_code=201)
def create_downtime(body: DowntimeCreate, db: Session = Depends(get_db)) -> dict:
    machine = get_or_404(db, Machine, body.machine_id, "Machine")
    event = DowntimeEvent(
        **{
            **body.model_dump(),
            "start_time": body.start_time.astimezone(timezone.utc),
            "end_time": body.end_time.astimezone(timezone.utc),
        }
    )
    db.add(event)
    db.flush()
    _audit(db, machine.work_center.facility, "downtime", event.id, "created")
    _commit(db)
    return serialize_downtime(event)


@router.delete("/downtime/{downtime_id}", status_code=204)
def delete_downtime(downtime_id: str, db: Session = Depends(get_db)) -> None:
    event = get_or_404(db, DowntimeEvent, downtime_id, "Downtime")
    _audit(db, event.machine.work_center.facility, "downtime", event.id, "deleted")
    db.delete(event)
    _commit(db)


@router.get("/jobs")
def list_jobs(
    facility_id: str | None = None,
    search: str | None = None,
    status: str | None = None,
    priority: int | None = Query(default=None, ge=1, le=10),
    sort: str = "job_number",
    descending: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    stmt = _job_query(facility_id)
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                ProductionJob.job_number.ilike(pattern),
                ProductionJob.product_name.ilike(pattern),
                ProductionJob.customer_reference.ilike(pattern),
            )
        )
    if status:
        stmt = stmt.where(ProductionJob.status == status)
    if priority:
        stmt = stmt.where(ProductionJob.priority == priority)
    fields = {
        "job_number": ProductionJob.job_number,
        "release_time": ProductionJob.release_time,
        "due_time": ProductionJob.due_time,
        "priority": ProductionJob.priority,
        "created_at": ProductionJob.created_at,
    }
    if sort not in fields:
        raise invalid("Unsupported sort field", {"allowed": list(fields)})
    order = fields[sort].desc() if descending else fields[sort].asc()
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(order, ProductionJob.id).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [serialize_job(item) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    return serialize_job(get_or_404(db, ProductionJob, job_id, "Job"))


def _validate_job_machine_options(db: Session, facility_id: str, operations) -> None:
    machine_ids = {option.machine_id for operation in operations for option in operation.options}
    valid_machine_ids = {
        machine.id
        for machine in _facility_machines(db, facility_id)
        if machine.active and machine.status == "available"
    }
    unknown = machine_ids - valid_machine_ids
    if unknown:
        raise invalid(
            "Operations reference unknown or inactive machines in this facility",
            {"machine_ids": sorted(unknown)},
        )


def _scenario_references_job(
    db: Session, facility_id: str, job_id: str, operation_ids: set[str] | None = None
) -> bool:
    for scenario in db.scalars(select(Scenario).where(Scenario.facility_id == facility_id)):
        for item in scenario.input_snapshot.get("jobs", []):
            if item.get("id") != job_id:
                continue
            if operation_ids is None:
                return True
            if any(operation.get("id") in operation_ids for operation in item.get("operations", [])):
                return True
    return False


def _attach_operations(job: ProductionJob, operations) -> None:
    for item in sorted(operations, key=lambda op: op.operation_number):
        operation = ProductionOperation(
            operation_number=item.operation_number,
            operation_name=item.operation_name,
            required_sequence=item.operation_number,
            default_processing_minutes=min(option.processing_minutes for option in item.options),
            setup_group=item.setup_group,
            status="planned",
        )
        job.operations.append(operation)
        for option in item.options:
            operation.options.append(
                OperationMachineOption(
                    machine_id=option.machine_id, processing_minutes=option.processing_minutes
                )
            )


@router.post("/jobs", status_code=201)
def create_job(body: JobCreate, db: Session = Depends(get_db)) -> dict:
    facility = get_or_404(db, Facility, body.facility_id, "Facility")
    _validate_job_machine_options(db, facility.id, body.operations)
    fields = body.model_dump(exclude={"operations"})
    fields["release_time"] = body.release_time.astimezone(timezone.utc)
    fields["due_time"] = body.due_time.astimezone(timezone.utc)
    job = ProductionJob(**fields)
    _attach_operations(job, body.operations)
    db.add(job)
    db.flush()
    _audit(db, facility, "job", job.id, "created", {"job_number": job.job_number})
    _commit(db, "Job number already exists in this facility")
    return serialize_job(job)


@router.patch("/jobs/{job_id}")
def update_job(job_id: str, body: JobUpdate, db: Session = Depends(get_db)) -> dict:
    job = get_or_404(db, ProductionJob, job_id, "Job")
    if job.scenario_id:
        raise ApiError(409, "scenario_resource", "Scenario-only orders cannot be edited as facility jobs")
    changes = body.model_dump(exclude_unset=True, exclude={"operations"})
    for key, value in changes.items():
        if key in {"release_time", "due_time"} and value is not None:
            value = value.astimezone(timezone.utc)
        setattr(job, key, value)
    if job.due_time <= job.release_time:
        raise invalid("Due time must follow release time")
    if body.operations is not None:
        _validate_job_machine_options(db, job.facility_id, body.operations)
        existing = {operation.operation_number: operation for operation in job.operations}
        submitted = {operation.operation_number for operation in body.operations}
        removed = [operation for number, operation in existing.items() if number not in submitted]
        if removed:
            used = db.scalar(
                select(func.count())
                .select_from(ScheduledOperation)
                .where(ScheduledOperation.operation_id.in_([operation.id for operation in removed]))
            )
            if used:
                raise ApiError(
                    409, "historical_reference", "A scheduled operation cannot be removed from this job"
                )
            if _scenario_references_job(db, job.facility_id, job.id, {operation.id for operation in removed}):
                raise ApiError(
                    409, "historical_reference", "An operation in a saved scenario cannot be removed"
                )
        for operation in removed:
            job.operations.remove(operation)
        for item in sorted(body.operations, key=lambda value: value.operation_number):
            operation = existing.get(item.operation_number)
            if operation is None:
                operation = ProductionOperation(operation_number=item.operation_number, status="planned")
                job.operations.append(operation)
            operation.operation_name = item.operation_name
            operation.required_sequence = item.operation_number
            operation.default_processing_minutes = min(option.processing_minutes for option in item.options)
            operation.setup_group = item.setup_group
            operation.options.clear()
            for option in item.options:
                operation.options.append(
                    OperationMachineOption(
                        machine_id=option.machine_id, processing_minutes=option.processing_minutes
                    )
                )
    _audit(
        db,
        job.facility,
        "job",
        job.id,
        "updated",
        {"fields": list(changes) + (["operations"] if body.operations is not None else [])},
    )
    _commit(db, "Job number or operation number already exists")
    return serialize_job(job)


@router.post("/jobs/{job_id}/duplicate", status_code=201)
def duplicate_job(job_id: str, db: Session = Depends(get_db)) -> dict:
    source = get_or_404(db, ProductionJob, job_id, "Job")
    if source.scenario_id:
        raise ApiError(
            409, "scenario_resource", "Scenario-only orders cannot be duplicated into the facility"
        )
    prefix = f"{source.job_number}-COPY"
    number = prefix
    suffix = 2
    while db.scalar(
        select(ProductionJob.id).where(
            ProductionJob.facility_id == source.facility_id, ProductionJob.job_number == number
        )
    ):
        number = f"{prefix}-{suffix}"
        suffix += 1
    job = ProductionJob(
        facility_id=source.facility_id,
        job_number=number,
        customer_reference=source.customer_reference,
        product_name=source.product_name,
        quantity=source.quantity,
        priority=source.priority,
        release_time=source.release_time,
        due_time=source.due_time,
        status="planned",
    )
    for item in source.operations:
        operation = ProductionOperation(
            operation_number=item.operation_number,
            operation_name=item.operation_name,
            required_sequence=item.required_sequence,
            default_processing_minutes=item.default_processing_minutes,
            setup_group=item.setup_group,
            status="planned",
        )
        job.operations.append(operation)
        for option in item.options:
            operation.options.append(
                OperationMachineOption(
                    machine_id=option.machine_id, processing_minutes=option.processing_minutes
                )
            )
    db.add(job)
    db.flush()
    _audit(db, source.facility, "job", job.id, "duplicated", {"source_job_id": source.id})
    _commit(db)
    return serialize_job(job)


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, db: Session = Depends(get_db)) -> None:
    job = get_or_404(db, ProductionJob, job_id, "Job")
    if job.scenario_id:
        raise ApiError(409, "scenario_resource", "Scenario-only orders cannot be deleted as facility jobs")
    ids = [operation.id for operation in job.operations]
    if ids and db.scalar(
        select(func.count()).select_from(ScheduledOperation).where(ScheduledOperation.operation_id.in_(ids))
    ):
        raise ApiError(
            409, "historical_reference", "This job is part of a saved schedule and cannot be deleted"
        )
    if _scenario_references_job(db, job.facility_id, job.id):
        raise ApiError(
            409, "historical_reference", "This job is part of a saved scenario and cannot be deleted"
        )
    _audit(db, job.facility, "job", job.id, "deleted", {"job_number": job.job_number})
    db.delete(job)
    _commit(db)
