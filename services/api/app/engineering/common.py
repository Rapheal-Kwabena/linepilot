"""Input normalization and interval arithmetic shared by engineering engines.

All engine times are integer minutes relative to snapshot ``origin``. The API
owns conversion between these offsets and timezone-aware UTC timestamps.
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any


class EngineeringInputError(ValueError):
    """A snapshot or engine configuration is structurally invalid."""


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EngineeringInputError(f"{label} must be an integer minute value")
    return value


def merge_intervals(intervals: list[list[int]] | list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Return sorted, non-overlapping half-open intervals."""
    merged: list[list[int]] = []
    for start, end in sorted((int(a), int(b)) for a, b in intervals if b > a):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def subtract_intervals(
    intervals: list[tuple[int, int]], removed: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Subtract unavailable intervals from operating intervals."""
    result: list[tuple[int, int]] = []
    excluded = merge_intervals(removed)
    for start, end in merge_intervals(intervals):
        cursor = start
        for cut_start, cut_end in excluded:
            if cut_end <= cursor:
                continue
            if cut_start >= end:
                break
            if cut_start > cursor:
                result.append((cursor, min(cut_start, end)))
            cursor = max(cursor, cut_end)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return result


def complement_intervals(intervals: list[tuple[int, int]], horizon: int) -> list[tuple[int, int]]:
    """Return unavailable intervals inside [0, horizon)."""
    return subtract_intervals([(0, horizon)], intervals)


def clipped_length(intervals: list[tuple[int, int]], start: int, end: int) -> int:
    if end <= start:
        return 0
    return sum(max(0, min(b, end) - max(a, start)) for a, b in intervals)


def interval_fits(intervals: list[tuple[int, int]], start: int, end: int) -> bool:
    return end > start and any(a <= start and end <= b for a, b in intervals)


def earliest_fit(
    intervals: list[tuple[int, int]],
    occupied: list[tuple[int, int]],
    ready: int,
    duration: int,
) -> int | None:
    """Find the earliest non-preemptive slot in operating and idle time."""
    if duration <= 0:
        return None
    free = subtract_intervals(intervals, occupied)
    for start, end in free:
        candidate = max(start, ready)
        if candidate + duration <= end:
            return candidate
    return None


def normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate a JSON-compatible facility snapshot and derive free windows.

    The returned copy has operations in sequence order, merged availability and
    downtime, and ``free_windows`` on each machine. No caller data is mutated.
    """
    if not isinstance(snapshot, dict):
        raise EngineeringInputError("snapshot must be an object")
    data = deepcopy(snapshot)
    horizon = _integer(data.get("horizon_minutes"), "horizon_minutes")
    if not 1 <= horizon <= 525_600:
        raise EngineeringInputError("horizon_minutes must be between 1 and 525600")
    data["horizon_minutes"] = horizon
    if not isinstance(data.get("origin"), str) or not data["origin"]:
        raise EngineeringInputError("origin must be an ISO UTC timestamp")
    try:
        origin = datetime.fromisoformat(data["origin"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise EngineeringInputError("origin must be an ISO UTC timestamp") from exc
    if origin.tzinfo is None or origin.utcoffset() != timedelta(0):
        raise EngineeringInputError("origin must include a UTC timezone offset")

    machines = data.get("machines")
    jobs = data.get("jobs")
    if not isinstance(machines, list) or not isinstance(jobs, list):
        raise EngineeringInputError("machines and jobs must be arrays")
    machine_ids: set[str] = set()
    for machine in machines:
        if not isinstance(machine, dict) or not machine.get("id"):
            raise EngineeringInputError("every machine needs an id")
        machine_id = str(machine["id"])
        if machine_id in machine_ids:
            raise EngineeringInputError(f"duplicate machine id {machine_id}")
        machine_ids.add(machine_id)
        machine["id"] = machine_id
        machine["code"] = str(machine.get("code") or machine_id)
        raw_availability = machine.get("availability", [[0, horizon]])
        raw_downtime = machine.get("downtime", [])
        if not isinstance(raw_availability, list) or not isinstance(raw_downtime, list):
            raise EngineeringInputError(f"machine {machine_id} availability and downtime must be arrays")
        for label, spans in (("availability", raw_availability), ("downtime", raw_downtime)):
            for span in spans:
                if not isinstance(span, (list, tuple)) or len(span) != 2:
                    raise EngineeringInputError(f"machine {machine_id} {label} intervals need start and end")
                a = _integer(span[0], f"machine {machine_id} {label} start")
                b = _integer(span[1], f"machine {machine_id} {label} end")
                if a >= b:
                    raise EngineeringInputError(
                        f"machine {machine_id} {label} interval must have positive length"
                    )
        availability = merge_intervals([(max(0, a), min(horizon, b)) for a, b in raw_availability])
        downtime = merge_intervals([(max(0, a), min(horizon, b)) for a, b in raw_downtime])
        machine["availability"] = [[a, b] for a, b in availability]
        machine["downtime"] = [[a, b] for a, b in downtime]
        machine["free_windows"] = subtract_intervals(availability, downtime)
        cost = machine.get("hourly_cost", 0)
        if (
            isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or not math.isfinite(cost)
            or cost < 0
        ):
            raise EngineeringInputError(f"machine {machine_id} hourly_cost must be finite and nonnegative")
        machine["hourly_cost"] = float(cost)

    job_ids: set[str] = set()
    job_numbers: set[str] = set()
    operation_ids: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict) or not job.get("id"):
            raise EngineeringInputError("every job needs an id")
        job_id = str(job["id"])
        job_number = str(job.get("job_number") or job_id)
        if job_id in job_ids or job_number in job_numbers:
            raise EngineeringInputError(f"duplicate job id or number {job_number}")
        job_ids.add(job_id)
        job_numbers.add(job_number)
        job["id"] = job_id
        job["job_number"] = job_number
        job["release_min"] = _integer(job.get("release_min", 0), f"job {job_number} release_min")
        job["due_min"] = _integer(job.get("due_min", horizon), f"job {job_number} due_min")
        priority = _integer(job.get("priority", 1), f"job {job_number} priority")
        if priority <= 0:
            raise EngineeringInputError(f"job {job_number} priority must be positive")
        job["priority"] = priority
        operations = job.get("operations")
        if not isinstance(operations, list) or not operations:
            raise EngineeringInputError(f"job {job_number} needs at least one operation")
        sequence_numbers: set[int] = set()
        for operation in operations:
            if not isinstance(operation, dict) or not operation.get("id"):
                raise EngineeringInputError(f"job {job_number} has an operation without an id")
            op_id = str(operation["id"])
            if op_id in operation_ids:
                raise EngineeringInputError(f"duplicate operation id {op_id}")
            operation_ids.add(op_id)
            operation["id"] = op_id
            number = _integer(operation.get("operation_number"), f"operation {op_id} number")
            if number <= 0 or number in sequence_numbers:
                raise EngineeringInputError(
                    f"job {job_number} operation numbers must be distinct positive integers"
                )
            sequence_numbers.add(number)
            options = operation.get("options")
            if not isinstance(options, list) or not options:
                raise EngineeringInputError(f"operation {op_id} needs at least one machine option")
            seen_options: set[str] = set()
            for option in options:
                if not isinstance(option, dict):
                    raise EngineeringInputError(f"operation {op_id} has an invalid machine option")
                machine_id = str(option.get("machine_id") or "")
                if machine_id not in machine_ids or machine_id in seen_options:
                    raise EngineeringInputError(
                        f"operation {op_id} has an unknown or duplicate machine option {machine_id}"
                    )
                seen_options.add(machine_id)
                duration = _integer(option.get("processing_minutes"), f"operation {op_id} duration")
                if duration <= 0:
                    raise EngineeringInputError(f"operation {op_id} duration must be positive")
                option["machine_id"] = machine_id
            operation["name"] = str(operation.get("name") or op_id)
        operations.sort(key=lambda op: op["operation_number"])
    return data


def snapshot_lookup(
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    machines = {machine["id"]: machine for machine in snapshot["machines"]}
    jobs = {job["id"]: job for job in snapshot["jobs"]}
    operations = {
        operation["id"]: (job, operation) for job in snapshot["jobs"] for operation in job["operations"]
    }
    return machines, jobs, operations
