"""Build immutable, minute-based engineering inputs from persisted production data."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from math import ceil, floor, isfinite
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Facility, Machine, ProductionJob, ProductionOperation, WorkCenter

UTC = timezone.utc


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive UTC values even for timezone-aware columns."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iso_utc(value: datetime) -> str:
    return as_utc(value).isoformat().replace("+00:00", "Z")


def parse_aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include a UTC offset")
    return parsed.astimezone(UTC)


def minute_offset(value: datetime, origin: datetime, *, round_up: bool = False) -> int:
    value_in_minutes = (as_utc(value) - as_utc(origin)).total_seconds() / 60
    return ceil(value_in_minutes) if round_up else floor(value_in_minutes)


def absolute_time(origin: str, minute: float) -> str:
    return iso_utc(parse_aware(origin) + timedelta(minutes=minute))


def scenario_machine_id(scenario_id: str, index: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"{scenario_id}:machine:{index}"))


def scenario_machine_code(requested: str, scenario_id: str) -> str:
    return f"{requested[:30]}-{scenario_id[:6]}"


def scenario_job_id(scenario_id: str, index: int, number: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"{scenario_id}:job:{index}:{number}"))


def scenario_operation_id(job_id: str, operation_number: int) -> str:
    return str(uuid5(NAMESPACE_URL, f"{job_id}:operation:{operation_number}"))


def _merge(intervals: list[list[int]]) -> list[list[int]]:
    result: list[list[int]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def _subtract(intervals: list[list[int]], closures: list[list[int]]) -> list[list[int]]:
    remaining: list[list[int]] = []
    for start, end in _merge(intervals):
        cursor = start
        for close_start, close_end in _merge(closures):
            if close_end <= cursor:
                continue
            if close_start >= end:
                break
            if close_start > cursor:
                remaining.append([cursor, min(close_start, end)])
            cursor = max(cursor, close_end)
            if cursor >= end:
                break
        if cursor < end:
            remaining.append([cursor, end])
    return remaining


def _clip_interval(start: datetime, end: datetime, origin: datetime, horizon: int) -> list[int] | None:
    a = max(0, minute_offset(start, origin, round_up=True))
    b = min(horizon, minute_offset(end, origin))
    return [a, b] if b > a else None


def _local_boundary(day: date, clock: time, zone: ZoneInfo, *, ending: bool) -> datetime:
    """Resolve DST boundaries: first fold for start, last fold for end.

    A nonexistent spring-forward wall time advances to the first valid minute.
    This keeps every expanded availability window unambiguous in UTC.
    """
    naive = datetime.combine(day, clock)
    for _ in range(181):
        candidates = []
        for fold in (0, 1):
            aware = naive.replace(tzinfo=zone, fold=fold)
            round_trip = aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
            if round_trip == naive:
                candidates.append(aware)
        if candidates:
            return (
                max(candidates, key=lambda value: value.astimezone(UTC))
                if ending
                else min(candidates, key=lambda value: value.astimezone(UTC))
            )
        naive += timedelta(minutes=1)
    raise ValueError("Calendar shift boundary cannot be resolved in the configured timezone")


def _calendar_availability(calendar, origin: datetime, horizon: int) -> list[list[int]]:
    if calendar is None:
        return [[0, horizon]]
    zone = ZoneInfo(calendar.timezone)
    origin_local = origin.astimezone(zone)
    end_local = (origin + timedelta(minutes=horizon)).astimezone(zone)
    first_date = origin_local.date() - timedelta(days=1)
    last_date = end_local.date() + timedelta(days=1)
    holidays = {str(item) for item in calendar.holiday_exceptions or []}
    intervals: list[list[int]] = []
    day = first_date
    while day <= last_date:
        if day.isoformat() not in holidays:
            for shift in calendar.weekly_shifts or []:
                if shift.get("day") != day.weekday():
                    continue
                start_clock = time.fromisoformat(shift["start"])
                end_clock = time.fromisoformat(shift["end"])
                start_local = _local_boundary(day, start_clock, zone, ending=False)
                end_day = day + timedelta(days=1) if end_clock <= start_clock else day
                end_local_shift = _local_boundary(end_day, end_clock, zone, ending=True)
                clipped = _clip_interval(
                    start_local.astimezone(UTC), end_local_shift.astimezone(UTC), origin, horizon
                )
                if clipped:
                    intervals.append(clipped)
        day += timedelta(days=1)
    return _merge(intervals)


def _calendar_closures(calendar, origin: datetime, horizon: int) -> list[list[int]]:
    if calendar is None:
        return []
    intervals: list[list[int]] = []
    zone = ZoneInfo(calendar.timezone)
    for holiday in calendar.holiday_exceptions or []:
        day = date.fromisoformat(str(holiday))
        start = _local_boundary(day, time(0, 0), zone, ending=False).astimezone(UTC)
        end = _local_boundary(day + timedelta(days=1), time(0, 0), zone, ending=True).astimezone(UTC)
        clipped = _clip_interval(start, end, origin, horizon)
        if clipped:
            intervals.append(clipped)
    for closure in calendar.special_closures or []:
        if "start_time" not in closure or "end_time" not in closure:
            continue
        clipped = _clip_interval(
            parse_aware(closure["start_time"]), parse_aware(closure["end_time"]), origin, horizon
        )
        if clipped:
            intervals.append(clipped)
    return _merge(intervals)


def build_snapshot(
    db: Session,
    facility: Facility,
    *,
    horizon_hours: int = 336,
    job_ids: list[str] | None = None,
    origin_override: datetime | None = None,
) -> dict:
    """Return only persisted engineering inputs, with all times in UTC-relative minutes."""
    jobs = db.scalars(
        select(ProductionJob)
        .where(
            ProductionJob.facility_id == facility.id,
            ProductionJob.scenario_id.is_(None),
            ProductionJob.status.in_(["planned", "released"]),
        )
        .options(selectinload(ProductionJob.operations).selectinload(ProductionOperation.options))
        .order_by(ProductionJob.release_time, ProductionJob.job_number)
    ).all()
    if job_ids is not None:
        wanted = set(job_ids)
        found = {job.id for job in jobs}
        missing = wanted - found
        if missing:
            raise ValueError(f"Unknown or unschedulable jobs for facility: {', '.join(sorted(missing))}")
        jobs = [job for job in jobs if job.id in wanted]
    if not jobs:
        raise ValueError("Facility has no selected production jobs")
    if len(jobs) > 200:
        raise ValueError("Scheduling is limited to 200 jobs per run")

    if origin_override:
        origin = as_utc(origin_override)
    else:
        earliest = min(as_utc(job.release_time) for job in jobs)
        origin = earliest.replace(second=0, microsecond=0)
    horizon = horizon_hours * 60

    work_centers = db.scalars(
        select(WorkCenter)
        .where(WorkCenter.facility_id == facility.id)
        .options(selectinload(WorkCenter.machines).selectinload(Machine.downtime))
        .order_by(WorkCenter.display_order, WorkCenter.code)
    ).all()
    machines = [
        machine
        for center in work_centers
        for machine in center.machines
        if machine.active and machine.status == "available" and machine.scenario_id is None
    ]
    if not machines:
        raise ValueError("Facility has no active machines")
    if len(machines) > 100:
        raise ValueError("Scheduling is limited to 100 active machines per run")
    machine_ids = {machine.id for machine in machines}
    snapshot_machines: list[dict] = []
    for machine in machines:
        calendar = machine.operating_calendar or facility.operating_calendar
        availability = _subtract(
            _calendar_availability(calendar, origin, horizon),
            _calendar_closures(calendar, origin, horizon),
        )
        downtime: list[list[int]] = []
        for event in machine.downtime:
            clipped = _clip_interval(as_utc(event.start_time), as_utc(event.end_time), origin, horizon)
            if clipped:
                downtime.append(clipped)
        snapshot_machines.append(
            {
                "id": machine.id,
                "code": machine.machine_code,
                "work_center_id": machine.work_center_id,
                "hourly_cost": machine.hourly_operating_cost,
                "availability": availability,
                "downtime": _merge(downtime),
            }
        )

    snapshot_jobs: list[dict] = []
    for job in jobs:
        operations: list[dict] = []
        for operation in job.operations:
            options = [
                {"machine_id": option.machine_id, "processing_minutes": option.processing_minutes}
                for option in operation.options
                if option.machine_id in machine_ids
            ]
            if not options:
                raise ValueError(
                    f"{job.job_number} operation {operation.operation_number} has no active eligible machine"
                )
            operations.append(
                {
                    "id": operation.id,
                    "operation_number": operation.operation_number,
                    "name": operation.operation_name,
                    "options": options,
                }
            )
        if not operations:
            raise ValueError(f"{job.job_number} has no operations")
        snapshot_jobs.append(
            {
                "id": job.id,
                "job_number": job.job_number,
                "customer_reference": job.customer_reference,
                "product_name": job.product_name,
                "quantity": job.quantity,
                "release_min": minute_offset(job.release_time, origin, round_up=True),
                "due_min": minute_offset(job.due_time, origin),
                "priority": job.priority,
                "operations": operations,
            }
        )
    return {
        "origin": iso_utc(origin),
        "horizon_minutes": horizon,
        "machines": snapshot_machines,
        "jobs": snapshot_jobs,
        "facility_id": facility.id,
        "facility_name": facility.facility_name,
        "facility_timezone": facility.timezone,
        "sample_data": facility.sample_data,
    }


def apply_modifications(snapshot: dict, modifications: list[dict], *, scenario_id: str = "preview") -> dict:
    """Apply scenario changes to a copy, so saved facility and baseline remain immutable."""
    result = deepcopy(snapshot)
    origin = parse_aware(result["origin"])
    horizon = result["horizon_minutes"]
    machines = {machine["id"]: machine for machine in result["machines"]}
    jobs = {job["id"]: job for job in result["jobs"]}
    for index, modification in enumerate(modifications):
        kind = modification.get("type")
        if kind == "machine_failure":
            machine = machines.get(modification.get("machine_id"))
            if machine is None:
                raise ValueError(f"Modification {index + 1}: unknown machine")
            try:
                start = parse_aware(modification["start_time"])
                end = parse_aware(modification["end_time"])
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"Modification {index + 1}: valid start_time and end_time are required"
                ) from exc
            if end <= start:
                raise ValueError(f"Modification {index + 1}: end_time must follow start_time")
            clipped = _clip_interval(start, end, origin, horizon)
            if not clipped:
                raise ValueError(f"Modification {index + 1}: downtime is outside the scheduling horizon")
            machine["downtime"] = _merge(machine["downtime"] + [clipped])
        elif kind == "processing_change":
            operation_id = modification.get("operation_id")
            multiplier = modification.get("multiplier")
            if (
                not isinstance(multiplier, (float, int))
                or not isfinite(multiplier)
                or multiplier <= 0
                or multiplier > 10
            ):
                raise ValueError(f"Modification {index + 1}: multiplier must be above 0 and at most 10")
            operation = next(
                (op for job in result["jobs"] for op in job["operations"] if op["id"] == operation_id), None
            )
            if operation is None:
                raise ValueError(f"Modification {index + 1}: unknown operation")
            for option in operation["options"]:
                option["processing_minutes"] = max(1, ceil(option["processing_minutes"] * multiplier))
        elif kind == "priority_change":
            job = jobs.get(modification.get("job_id"))
            priority = modification.get("priority")
            if job is None or not isinstance(priority, int) or not 1 <= priority <= 10:
                raise ValueError(f"Modification {index + 1}: valid job_id and priority 1–10 are required")
            job["priority"] = priority
        elif kind == "add_capacity":
            source = machines.get(modification.get("source_machine_id"))
            if source is None:
                raise ValueError(f"Modification {index + 1}: unknown source machine")
            new_machine = deepcopy(source)
            new_machine["id"] = scenario_machine_id(scenario_id, index)
            requested = str(modification.get("machine_code") or f"{source['code']}-EXTRA")
            new_machine["code"] = scenario_machine_code(requested, scenario_id)
            if any(item["code"] == new_machine["code"] for item in result["machines"]):
                raise ValueError(f"Modification {index + 1}: machine code already exists")
            if "hourly_cost" in modification:
                cost = modification["hourly_cost"]
                if not isinstance(cost, (int, float)) or not isfinite(cost) or cost < 0:
                    raise ValueError(f"Modification {index + 1}: hourly_cost must be nonnegative")
                new_machine["hourly_cost"] = float(cost)
            new_machine["downtime"] = []
            result["machines"].append(new_machine)
            machines[new_machine["id"]] = new_machine
            for job in result["jobs"]:
                for operation in job["operations"]:
                    source_option = next(
                        (option for option in operation["options"] if option["machine_id"] == source["id"]),
                        None,
                    )
                    if source_option:
                        operation["options"].append(
                            {
                                "machine_id": new_machine["id"],
                                "processing_minutes": source_option["processing_minutes"],
                            }
                        )
        elif kind == "order_surge":
            template = jobs.get(modification.get("template_job_id"))
            count = modification.get("count")
            if template is None or not isinstance(count, int) or not 1 <= count <= 20:
                raise ValueError(
                    f"Modification {index + 1}: valid template_job_id and count 1–20 are required"
                )
            for number in range(count):
                new_job = deepcopy(template)
                new_job["id"] = scenario_job_id(scenario_id, index, number)
                new_job["job_number"] = (
                    f"{template['job_number'][:38]}-S{index + 1}-{number + 1}-{scenario_id[:6]}"
                )
                for operation in new_job["operations"]:
                    operation["id"] = scenario_operation_id(new_job["id"], operation["operation_number"])
                result["jobs"].append(new_job)
                jobs[new_job["id"]] = new_job
        elif kind == "shift_adjustment":
            machine = machines.get(modification.get("machine_id"))
            mode = modification.get("mode")
            if machine is None or mode not in {"add", "close"}:
                raise ValueError(
                    f"Modification {index + 1}: valid machine_id and mode add/close are required"
                )
            try:
                start = parse_aware(modification["start_time"])
                end = parse_aware(modification["end_time"])
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"Modification {index + 1}: valid start_time and end_time are required"
                ) from exc
            if end <= start:
                raise ValueError(f"Modification {index + 1}: end_time must follow start_time")
            clipped = _clip_interval(start, end, origin, horizon)
            if not clipped:
                raise ValueError(f"Modification {index + 1}: adjustment is outside the horizon")
            if mode == "add":
                machine["availability"] = _merge(machine["availability"] + [clipped])
            else:
                machine["availability"] = _subtract(machine["availability"], [clipped])
        else:
            raise ValueError(f"Modification {index + 1}: unsupported type {kind!r}")
    if len(result["jobs"]) > 200:
        raise ValueError("Scenario scheduling is limited to 200 jobs")
    if len(result["machines"]) > 100:
        raise ValueError("Scenario scheduling is limited to 100 machines")
    return result
