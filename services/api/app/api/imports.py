"""Transactional CSV preview, validation, templates, and import records."""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.http import get_or_404, invalid
from app.models import (
    DowntimeEvent,
    Facility,
    ImportBatch,
    Machine,
    OperationMachineOption,
    ProductionJob,
    ProductionOperation,
    WorkCenter,
)
from app.schemas import ImportRequest
from app.serializers import serialize_import
from app.snapshot import parse_aware

router = APIRouter()

REQUIRED = {
    "jobs": [
        "job_number",
        "product_name",
        "quantity",
        "priority",
        "release_time",
        "due_time",
        "operation_number",
        "operation_name",
        "machine_code",
        "processing_minutes",
    ],
    "operations": ["job_number", "operation_number", "operation_name", "machine_code", "processing_minutes"],
    "machines": ["machine_code", "machine_name", "work_center_code"],
    "downtime": ["machine_code", "start_time", "end_time"],
}

TEMPLATES = {
    "jobs": (
        "job_number,product_name,quantity,priority,release_time,due_time,operation_number,operation_name,machine_code,processing_minutes,customer_reference\n"
        "JOB-501,Valve bracket,20,3,2026-11-16T06:00:00-06:00,2026-11-16T18:00:00-06:00,10,Cut blank,M-01,55,PO-501\n"
        "JOB-501,Valve bracket,20,3,2026-11-16T06:00:00-06:00,2026-11-16T18:00:00-06:00,10,Cut blank,M-04,62,PO-501\n"
        "JOB-501,Valve bracket,20,3,2026-11-16T06:00:00-06:00,2026-11-16T18:00:00-06:00,20,Form component,M-02,75,PO-501\n"
    ),
    "operations": (
        "job_number,operation_number,operation_name,machine_code,processing_minutes,setup_group\n"
        "JOB-101,40,Secondary inspection,M-03,45,FINAL\n"
        "JOB-101,40,Secondary inspection,M-06,53,FINAL\n"
    ),
    "machines": "machine_code,machine_name,work_center_code,machine_type,hourly_operating_cost,active\nM-07,Cutting Cell 3,CUT,laser cutting,55,true\n",
    "downtime": "machine_code,start_time,end_time,reason,event_type\nM-03,2026-11-16T11:00:00-06:00,2026-11-16T13:00:00-06:00,Planned inspection,planned_maintenance\n",
}


def _parse_rows(body: ImportRequest) -> tuple[list[str], list[dict], list[dict]]:
    reader = csv.DictReader(io.StringIO(body.csv_text.lstrip("\ufeff"), newline=""))
    columns = reader.fieldnames or []
    errors: list[dict] = []
    if len(columns) != len(set(columns)):
        errors.append({"row": 1, "field": "header", "message": "Duplicate column names are not allowed"})
    missing = [column for column in REQUIRED[body.category] if column not in columns]
    for column in missing:
        errors.append({"row": 1, "field": column, "message": "Required column is missing"})
    rows = []
    for line, raw in enumerate(reader, start=2):
        if line > 5001:
            errors.append({"row": line, "field": "file", "message": "CSV is limited to 5,000 data rows"})
            break
        if None in raw:
            errors.append({"row": line, "field": "row", "message": "Row has more values than the header"})
        row = {str(key): (value or "").strip() for key, value in raw.items() if key is not None}
        if any(row.values()):
            rows.append({"line": line, **row})
    if not rows:
        errors.append({"row": 2, "field": "file", "message": "CSV contains no data rows"})
    return columns, rows, errors


def _error(errors: list[dict], row: int, field: str, message: str) -> None:
    errors.append({"row": row, "field": field, "message": message})


def _length(value: str, row: int, field: str, maximum: int, errors: list[dict]) -> None:
    if len(value) > maximum:
        _error(errors, row, field, f"Must be at most {maximum} characters")


def _positive_int(
    value: str, row: int, field: str, errors: list[dict], *, maximum: int | None = None
) -> int | None:
    try:
        number = int(value)
        if number <= 0 or (maximum is not None and number > maximum):
            raise ValueError
        return number
    except (TypeError, ValueError):
        suffix = f" and at most {maximum}" if maximum is not None else ""
        _error(errors, row, field, f"Must be a positive integer{suffix}")
        return None


def _aware_datetime(value: str, row: int, field: str, errors: list[dict]) -> datetime | None:
    try:
        return parse_aware(value)
    except (TypeError, ValueError):
        _error(errors, row, field, "Use an ISO 8601 timestamp with a UTC offset")
        return None


def _validate(db: Session, body: ImportRequest) -> dict:
    facility = get_or_404(db, Facility, body.facility_id, "Facility")
    columns, rows, errors = _parse_rows(body)
    if errors and any(
        item["field"] == "header" or item["message"] == "Required column is missing" for item in errors
    ):
        return {"facility": facility, "columns": columns, "rows": rows, "errors": errors, "records": []}
    records = []
    if body.category == "operations":
        jobs = {
            job.job_number: job
            for job in db.scalars(
                select(ProductionJob).where(
                    ProductionJob.facility_id == facility.id,
                    ProductionJob.scenario_id.is_(None),
                    ProductionJob.status.in_(["planned", "released"]),
                )
            )
        }
        machines = {
            machine.machine_code: machine
            for machine in db.scalars(
                select(Machine)
                .join(WorkCenter)
                .where(
                    WorkCenter.facility_id == facility.id,
                    Machine.active.is_(True),
                    Machine.status == "available",
                    Machine.scenario_id.is_(None),
                )
            )
        }
        grouped: dict[tuple[str, int], dict] = {}
        seen_options = set()
        existing_numbers = {
            job.id: {operation.operation_number for operation in job.operations} for job in jobs.values()
        }
        for row in rows:
            line = row["line"]
            job = jobs.get(row.get("job_number", ""))
            _length(row.get("job_number", ""), line, "job_number", 60, errors)
            if job is None:
                _error(errors, line, "job_number", "Unknown or unschedulable job in this facility")
            number = _positive_int(
                row.get("operation_number", ""), line, "operation_number", errors, maximum=10000
            )
            if job and number in existing_numbers[job.id]:
                _error(errors, line, "operation_number", "Operation number already exists for this job")
            elif job and number is not None and number <= max(existing_numbers[job.id], default=0):
                _error(
                    errors, line, "operation_number", "New operation number must follow the existing sequence"
                )
            name = row.get("operation_name", "")
            _length(name, line, "operation_name", 120, errors)
            _length(row.get("setup_group", ""), line, "setup_group", 80, errors)
            if not name:
                _error(errors, line, "operation_name", "Operation name is required")
            machine_code = row.get("machine_code", "")
            machine = machines.get(machine_code)
            if machine is None:
                _error(errors, line, "machine_code", "Unknown or unavailable machine in this facility")
            processing = _positive_int(
                row.get("processing_minutes", ""), line, "processing_minutes", errors, maximum=10080
            )
            key = (row.get("job_number", ""), number, machine_code)
            if key in seen_options:
                _error(errors, line, "machine_code", "Duplicate machine option for this operation")
            seen_options.add(key)
            if not all([job, number, name, machine, processing]):
                continue
            group_key = (job.id, number)
            operation = grouped.setdefault(
                group_key,
                {
                    "job_id": job.id,
                    "operation_number": number,
                    "operation_name": name,
                    "setup_group": row.get("setup_group") or "",
                    "options": [],
                },
            )
            if operation["operation_name"] != name or operation["setup_group"] != (
                row.get("setup_group") or ""
            ):
                _error(
                    errors,
                    line,
                    "operation_name",
                    "Repeated operation rows must use the same name and setup group",
                )
            operation["options"].append({"machine_id": machine.id, "processing_minutes": processing})
        records = list(grouped.values())
        for record in records:
            if len(record["options"]) > 20:
                _error(errors, 0, "machine_code", "Each operation may have at most 20 machine options")
    elif body.category == "machines":
        centers = {
            center.code: center
            for center in db.scalars(select(WorkCenter).where(WorkCenter.facility_id == facility.id))
        }
        existing = set(db.scalars(select(Machine.machine_code)).all())
        seen = set()
        for row in rows:
            line = row["line"]
            code = row.get("machine_code", "")
            _length(code, line, "machine_code", 40, errors)
            _length(row.get("machine_name", ""), line, "machine_name", 120, errors)
            _length(row.get("machine_type", ""), line, "machine_type", 80, errors)
            center = centers.get(row.get("work_center_code", ""))
            if not code:
                _error(errors, line, "machine_code", "Machine code is required")
            if code in existing or code in seen:
                _error(errors, line, "machine_code", "Machine code already exists or is repeated in the CSV")
            seen.add(code)
            if not row.get("machine_name"):
                _error(errors, line, "machine_name", "Machine name is required")
            if center is None:
                _error(errors, line, "work_center_code", "Unknown work center in this facility")
            try:
                cost = float(row.get("hourly_operating_cost") or 0)
                if cost < 0 or not math.isfinite(cost):
                    raise ValueError
            except ValueError:
                cost = 0
                _error(errors, line, "hourly_operating_cost", "Must be a nonnegative number")
            active_text = (row.get("active") or "true").lower()
            if active_text not in {"true", "false", "1", "0", "yes", "no"}:
                _error(errors, line, "active", "Use true or false")
            if center and code:
                records.append(
                    {
                        "work_center_id": center.id,
                        "machine_code": code,
                        "machine_name": row.get("machine_name", ""),
                        "machine_type": row.get("machine_type") or "general",
                        "hourly_operating_cost": cost,
                        "active": active_text in {"true", "1", "yes"},
                    }
                )
    elif body.category == "downtime":
        machines = {
            machine.machine_code: machine
            for machine in db.scalars(
                select(Machine)
                .join(WorkCenter)
                .where(WorkCenter.facility_id == facility.id, Machine.scenario_id.is_(None))
            )
        }
        for row in rows:
            line = row["line"]
            _length(row.get("reason", ""), line, "reason", 160, errors)
            _length(row.get("event_type", ""), line, "event_type", 40, errors)
            machine = machines.get(row.get("machine_code", ""))
            if machine is None:
                _error(errors, line, "machine_code", "Unknown machine in this facility")
            start = _aware_datetime(row.get("start_time", ""), line, "start_time", errors)
            end = _aware_datetime(row.get("end_time", ""), line, "end_time", errors)
            if start and end and end <= start:
                _error(errors, line, "end_time", "End must follow start")
            if machine and start and end:
                records.append(
                    {
                        "machine_id": machine.id,
                        "start_time": start,
                        "end_time": end,
                        "reason": row.get("reason") or "Imported downtime",
                        "event_type": row.get("event_type") or "planned_maintenance",
                    }
                )
    else:
        machines = {
            machine.machine_code: machine
            for machine in db.scalars(
                select(Machine)
                .join(WorkCenter)
                .where(
                    WorkCenter.facility_id == facility.id,
                    Machine.active.is_(True),
                    Machine.status == "available",
                    Machine.scenario_id.is_(None),
                )
            )
        }
        existing = set(
            db.scalars(select(ProductionJob.job_number).where(ProductionJob.facility_id == facility.id)).all()
        )
        grouped: dict[str, dict] = {}
        seen_options = set()
        for row in rows:
            line = row["line"]
            job_number = row.get("job_number", "")
            _length(job_number, line, "job_number", 60, errors)
            _length(row.get("product_name", ""), line, "product_name", 160, errors)
            _length(row.get("customer_reference", ""), line, "customer_reference", 120, errors)
            _length(row.get("operation_name", ""), line, "operation_name", 120, errors)
            if not job_number:
                _error(errors, line, "job_number", "Job number is required")
            if job_number in existing:
                _error(errors, line, "job_number", "Job number already exists in this facility")
            if not row.get("product_name"):
                _error(errors, line, "product_name", "Product name is required")
            quantity = _positive_int(row.get("quantity", ""), line, "quantity", errors, maximum=2_147_483_647)
            priority = _positive_int(row.get("priority", ""), line, "priority", errors, maximum=10)
            op_number = _positive_int(
                row.get("operation_number", ""), line, "operation_number", errors, maximum=10000
            )
            processing = _positive_int(
                row.get("processing_minutes", ""), line, "processing_minutes", errors, maximum=10080
            )
            release = _aware_datetime(row.get("release_time", ""), line, "release_time", errors)
            due = _aware_datetime(row.get("due_time", ""), line, "due_time", errors)
            if release and due and due <= release:
                _error(errors, line, "due_time", "Due time must follow release time")
            machine_code = row.get("machine_code", "")
            machine = machines.get(machine_code)
            if machine is None:
                _error(errors, line, "machine_code", "Unknown or unavailable machine in this facility")
            if not row.get("operation_name"):
                _error(errors, line, "operation_name", "Operation name is required")
            option_key = (job_number, op_number, machine_code)
            if option_key in seen_options:
                _error(errors, line, "machine_code", "Duplicate machine option for this operation")
            seen_options.add(option_key)
            if not all(
                [
                    job_number,
                    row.get("product_name"),
                    quantity,
                    priority,
                    op_number,
                    processing,
                    release,
                    due,
                    machine,
                    row.get("operation_name"),
                ]
            ):
                continue
            metadata = {
                "job_number": job_number,
                "customer_reference": row.get("customer_reference") or "",
                "product_name": row["product_name"],
                "quantity": quantity,
                "priority": priority,
                "release_time": release,
                "due_time": due,
            }
            job = grouped.setdefault(job_number, {"metadata": metadata, "operations": {}})
            if job["metadata"] != metadata:
                _error(errors, line, "job_number", "Repeated job rows must use identical job fields")
            operation = job["operations"].setdefault(
                op_number,
                {"operation_number": op_number, "operation_name": row["operation_name"], "options": []},
            )
            if operation["operation_name"] != row["operation_name"]:
                _error(errors, line, "operation_name", "Repeated operation rows must use the same name")
            operation["options"].append({"machine_id": machine.id, "processing_minutes": processing})
        records = [
            {
                **group["metadata"],
                "operations": [group["operations"][number] for number in sorted(group["operations"])],
            }
            for group in grouped.values()
        ]
        for record in records:
            if len(record["operations"]) > 50:
                _error(errors, 0, "operation_number", "Each job may have at most 50 operations")
            for operation in record["operations"]:
                if len(operation["options"]) > 20:
                    _error(errors, 0, "machine_code", "Each operation may have at most 20 machine options")
    return {"facility": facility, "columns": columns, "rows": rows, "errors": errors, "records": records}


@router.post("/imports/preview")
def preview_import(body: ImportRequest, db: Session = Depends(get_db)) -> dict:
    result = _validate(db, body)
    return {
        "category": body.category,
        "detected_columns": result["columns"],
        "row_count": len(result["rows"]),
        "valid": not result["errors"],
        "errors": result["errors"],
        "preview": result["rows"][:20],
        "import_behavior": "all_or_nothing",
    }


@router.post("/imports", status_code=201)
def confirm_import(body: ImportRequest, db: Session = Depends(get_db)) -> dict:
    result = _validate(db, body)
    batch = ImportBatch(
        facility_id=body.facility_id,
        file_name=body.file_name,
        file_type="csv",
        category=body.category,
        detected_columns=result["columns"],
        import_status="rejected" if result["errors"] else "completed",
        validation_errors=result["errors"],
        rows_processed=0 if result["errors"] else len(result["rows"]),
        rows_failed=len(result["rows"]) if result["errors"] else 0,
    )
    if not result["errors"]:
        for record in result["records"]:
            if body.category == "machines":
                db.add(Machine(**record, status="available"))
            elif body.category == "downtime":
                db.add(DowntimeEvent(**record))
            elif body.category == "operations":
                options = record["options"]
                operation = ProductionOperation(
                    job_id=record["job_id"],
                    operation_number=record["operation_number"],
                    operation_name=record["operation_name"],
                    required_sequence=record["operation_number"],
                    default_processing_minutes=min(option["processing_minutes"] for option in options),
                    setup_group=record["setup_group"],
                    status="planned",
                )
                db.add(operation)
                for option in options:
                    db.add(OperationMachineOption(operation=operation, **option))
            else:
                operations = record.pop("operations")
                job = ProductionJob(facility_id=body.facility_id, status="planned", **record)
                db.add(job)
                for item in operations:
                    operation = ProductionOperation(
                        job=job,
                        operation_number=item["operation_number"],
                        operation_name=item["operation_name"],
                        required_sequence=item["operation_number"],
                        default_processing_minutes=min(
                            option["processing_minutes"] for option in item["options"]
                        ),
                        setup_group="",
                        status="planned",
                    )
                    db.add(operation)
                    for option in item["options"]:
                        db.add(OperationMachineOption(operation=operation, **option))
    db.add(batch)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        batch = ImportBatch(
            facility_id=body.facility_id,
            file_name=body.file_name,
            file_type="csv",
            category=body.category,
            detected_columns=result["columns"],
            import_status="failed",
            validation_errors=[
                {"row": 0, "field": "database", "message": "Import conflicted with data added concurrently"}
            ],
            rows_processed=0,
            rows_failed=len(result["rows"]),
        )
        db.add(batch)
        db.commit()
    return serialize_import(batch)


@router.get("/imports/templates/{category}")
def import_template(category: str) -> Response:
    if category not in TEMPLATES:
        raise invalid("Unsupported import category", {"allowed": list(TEMPLATES)})
    return Response(
        content=TEMPLATES[category],
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="linepilot-{category}-template.csv"'},
    )


@router.get("/imports")
def list_imports(
    facility_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(ImportBatch)
    if facility_id:
        stmt = stmt.where(ImportBatch.facility_id == facility_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(ImportBatch.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [serialize_import(item) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/imports/{import_id}")
def get_import(import_id: str, db: Session = Depends(get_db)) -> dict:
    return serialize_import(get_or_404(db, ImportBatch, import_id, "Import batch"))
