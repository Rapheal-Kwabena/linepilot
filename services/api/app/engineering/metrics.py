"""Canonical engineering metric definitions for persisted schedules."""

from __future__ import annotations

from typing import Any

from .common import clipped_length, normalize_snapshot, snapshot_lookup


def compute_schedule_metrics(snapshot: dict[str, Any], assignments: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate schedule results from assignments, never from solver claims.

    The observation period begins at minute zero and ends at the last completed
    operation. Machine utilization divides busy minutes by operating-calendar
    minutes in that period after planned downtime is removed. An empty
    denominator is reported as null, with a reason.
    """
    data = normalize_snapshot(snapshot)
    machines, _, operations = snapshot_lookup(data)
    by_operation = {item["operation_id"]: item for item in assignments}
    makespan = max((item["end_min"] for item in assignments), default=0)
    machine_busy = {machine_id: 0 for machine_id in machines}
    for item in assignments:
        if item["machine_id"] in machine_busy:
            machine_busy[item["machine_id"]] += item["end_min"] - item["start_min"]

    machine_metrics: list[dict[str, Any]] = []
    for machine in data["machines"]:
        machine_id = machine["id"]
        scheduled = clipped_length([(a, b) for a, b in machine["availability"]], 0, makespan)
        available = clipped_length(machine["free_windows"], 0, makespan)
        busy = machine_busy[machine_id]
        machine_metrics.append(
            {
                "machine_id": machine_id,
                "machine_code": machine["code"],
                "work_center_id": machine.get("work_center_id"),
                "busy_minutes": busy,
                "available_minutes": available,
                "idle_minutes": max(0, available - busy),
                "downtime_minutes": max(0, scheduled - available),
                "calendar_closed_minutes": max(0, makespan - scheduled),
                "utilization": busy / available if available else None,
                "operating_cost": busy * machine["hourly_cost"] / 60,
            }
        )

    job_metrics: list[dict[str, Any]] = []
    for job in data["jobs"]:
        items = [by_operation.get(operation["id"]) for operation in job["operations"]]
        if any(item is None for item in items):
            continue
        completed_items = [item for item in items if item is not None]
        completion = max(item["end_min"] for item in completed_items)
        processing = sum(item["end_min"] - item["start_min"] for item in completed_items)
        flow = completion - job["release_min"]
        tardiness = max(0, completion - job["due_min"])
        job_metrics.append(
            {
                "job_id": job["id"],
                "job_number": job["job_number"],
                "release_min": job["release_min"],
                "due_min": job["due_min"],
                "completion_min": completion,
                "processing_minutes": processing,
                "flow_minutes": flow,
                "waiting_minutes": max(0, flow - processing),
                "tardiness_minutes": tardiness,
                "weighted_tardiness_minutes": tardiness * job["priority"],
                "on_time": tardiness == 0,
                "priority": job["priority"],
            }
        )

    completed = len(job_metrics)
    on_time = sum(1 for item in job_metrics if item["on_time"])
    late = completed - on_time
    total_busy = sum(item["busy_minutes"] for item in machine_metrics)
    total_available = sum(item["available_minutes"] for item in machine_metrics)
    utilization_values = [item["utilization"] for item in machine_metrics if item["utilization"] is not None]
    metric_reasons: dict[str, str] = {}
    if not completed:
        metric_reasons["on_time_delivery_rate"] = "No completed jobs"
        metric_reasons["mean_job_flow_minutes"] = "No completed jobs"
        metric_reasons["mean_job_waiting_minutes"] = "No completed jobs"
    if not makespan:
        metric_reasons["throughput_jobs_per_hour"] = "No elapsed production period"
    if not utilization_values:
        metric_reasons["average_machine_utilization"] = "No available machine time"
    return {
        "observation_start_min": 0,
        "observation_end_min": makespan,
        "makespan_minutes": makespan,
        "scheduled_operations": len(assignments),
        "completed_jobs": completed,
        "on_time_jobs": on_time,
        "late_jobs": late,
        "total_processing_minutes": total_busy,
        "total_tardiness_minutes": sum(item["tardiness_minutes"] for item in job_metrics),
        "total_weighted_tardiness_minutes": sum(item["weighted_tardiness_minutes"] for item in job_metrics),
        "on_time_delivery_rate": on_time / completed if completed else None,
        "throughput_jobs_per_hour": completed * 60 / makespan if makespan else None,
        "mean_job_flow_minutes": (
            sum(item["flow_minutes"] for item in job_metrics) / completed if completed else None
        ),
        "mean_job_waiting_minutes": (
            sum(item["waiting_minutes"] for item in job_metrics) / completed if completed else None
        ),
        "average_machine_utilization": (
            sum(utilization_values) / len(utilization_values) if utilization_values else None
        ),
        "weighted_machine_utilization": total_busy / total_available if total_available else None,
        "operating_cost": sum(item["operating_cost"] for item in machine_metrics),
        "machine_metrics": machine_metrics,
        "job_metrics": job_metrics,
        "metric_reasons": metric_reasons,
    }
