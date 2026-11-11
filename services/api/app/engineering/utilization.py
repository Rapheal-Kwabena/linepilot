"""Time-window capacity analysis from a saved schedule and its input snapshot."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .common import clipped_length, normalize_snapshot


def _overlap(start: int, end: int, left: int, right: int) -> int:
    return max(0, min(end, right) - max(start, left))


def _machine_window(
    machine: dict[str, Any],
    assignments: list[dict[str, Any]],
    start: int,
    end: int,
) -> dict[str, Any]:
    availability = [tuple(window) for window in machine["availability"]]
    scheduled = clipped_length(availability, start, end)
    available = clipped_length(machine["free_windows"], start, end)
    busy = 0
    overtime = 0
    operations = 0
    for assignment in assignments:
        if assignment["machine_id"] != machine["id"]:
            continue
        occupied = _overlap(assignment["start_min"], assignment["end_min"], start, end)
        if not occupied:
            continue
        operations += 1
        busy += occupied
        clipped_start = max(start, assignment["start_min"])
        clipped_end = min(end, assignment["end_min"])
        overtime += max(0, occupied - clipped_length(availability, clipped_start, clipped_end))
    return {
        "machine_id": machine["id"],
        "machine_code": machine["code"],
        "work_center_id": machine.get("work_center_id"),
        "busy_minutes": busy,
        "available_minutes": available,
        "idle_minutes": max(0, available - busy),
        "unavailable_minutes": (end - start) - available,
        "downtime_minutes": max(0, scheduled - available),
        "calendar_closed_minutes": (end - start) - scheduled,
        "overtime_minutes": overtime,
        "utilization": busy / available if available else None,
        "scheduled_operation_count": operations,
        "operating_cost": busy * machine["hourly_cost"] / 60,
    }


def _bucket_size(duration: int) -> int:
    if duration <= 24 * 60:
        return 60
    if duration <= 7 * 24 * 60:
        return 240
    return max(1440, ((duration + 59 * 60 - 1) // (60 * 60)) * 60)


def compute_utilization_analysis(
    snapshot: dict[str, Any],
    assignments: list[dict[str, Any]],
    start_min: int,
    end_min: int,
) -> dict[str, Any]:
    """Measure busy, available, idle, downtime and closure in [start, end).

    The time series uses the same interval arithmetic as the summary. Its
    buckets are display aggregates of the saved schedule, not live telemetry.
    """
    data = normalize_snapshot(snapshot)
    horizon = data["horizon_minutes"]
    if not (0 <= start_min < end_min <= horizon):
        raise ValueError("Utilization window must be within the saved scheduling horizon")
    origin = datetime.fromisoformat(data["origin"].replace("Z", "+00:00"))

    def timestamp(minute: int) -> str:
        return (origin + timedelta(minutes=minute)).isoformat().replace("+00:00", "Z")

    items = [_machine_window(machine, assignments, start_min, end_min) for machine in data["machines"]]
    due_by_job = {job["id"]: job["due_min"] for job in data["jobs"]}
    for item in items:
        # This is a screening measure for the saved machine assignments. It
        # counts work on jobs due by the window end that was unfinished at the
        # window start, including operations that the schedule placed later.
        due_workload = sum(
            assignment["end_min"] - max(start_min, assignment["start_min"])
            for assignment in assignments
            if assignment["machine_id"] == item["machine_id"]
            and assignment["end_min"] > start_min
            and due_by_job.get(assignment["job_id"], horizon + 1) <= end_min
        )
        item["due_workload_minutes"] = due_workload
        item["due_capacity_gap_minutes"] = max(0, due_workload - item["available_minutes"])
    centers: dict[str, dict[str, Any]] = {}
    for item in items:
        center_id = item["work_center_id"] or ""
        center = centers.setdefault(
            center_id,
            {
                "work_center_id": center_id,
                "machine_count": 0,
                "busy_minutes": 0,
                "available_minutes": 0,
                "idle_minutes": 0,
                "downtime_minutes": 0,
            },
        )
        center["machine_count"] += 1
        for key in ("busy_minutes", "available_minutes", "idle_minutes", "downtime_minutes"):
            center[key] += item[key]
    for center in centers.values():
        center["utilization"] = (
            center["busy_minutes"] / center["available_minutes"] if center["available_minutes"] else None
        )

    totals = {
        key: sum(item[key] for item in items)
        for key in (
            "busy_minutes",
            "available_minutes",
            "idle_minutes",
            "unavailable_minutes",
            "downtime_minutes",
            "calendar_closed_minutes",
            "overtime_minutes",
            "due_workload_minutes",
            "due_capacity_gap_minutes",
        )
    }
    totals["utilization"] = (
        totals["busy_minutes"] / totals["available_minutes"] if totals["available_minutes"] else None
    )

    bucket_minutes = _bucket_size(end_min - start_min)
    series = []
    for bucket_start in range(start_min, end_min, bucket_minutes):
        bucket_end = min(end_min, bucket_start + bucket_minutes)
        bucket_items = [
            _machine_window(machine, assignments, bucket_start, bucket_end) for machine in data["machines"]
        ]
        series.append(
            {
                "start_min": bucket_start,
                "end_min": bucket_end,
                "start_time": timestamp(bucket_start),
                "end_time": timestamp(bucket_end),
                "items": [
                    {
                        "machine_id": item["machine_id"],
                        "busy_minutes": item["busy_minutes"],
                        "available_minutes": item["available_minutes"],
                        "utilization": item["utilization"],
                    }
                    for item in bucket_items
                ],
            }
        )

    return {
        "observation_start": data["origin"],
        "observation_start_min": start_min,
        "observation_end_min": end_min,
        "interval_start": timestamp(start_min),
        "interval_end": timestamp(end_min),
        "bucket_minutes": bucket_minutes,
        "items": items,
        "totals": totals,
        "work_centers": sorted(centers.values(), key=lambda center: center["work_center_id"]),
        "series": series,
        "metric_reasons": (
            {"utilization": "No available machine time in the selected interval"}
            if not totals["available_minutes"]
            else {}
        ),
    }
