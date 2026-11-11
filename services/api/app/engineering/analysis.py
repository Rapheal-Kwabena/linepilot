"""Evidence-based capacity and bottleneck candidates from stored runs."""

from __future__ import annotations

from typing import Any

from .common import normalize_snapshot, snapshot_lookup
from .metrics import compute_schedule_metrics
from .scheduling import validate_schedule


def analyze_bottlenecks(
    snapshot: dict[str, Any],
    assignments: list[dict[str, Any]],
    simulation_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Report utilization, queues, and late-job exposure as separate evidence.

    A high utilization rank is a candidate for further testing, not proof of a
    causal throughput bottleneck. Scenario comparisons provide sensitivity.
    """
    data = normalize_snapshot(snapshot)
    validation = validate_schedule(data, assignments)
    if not validation["valid"]:
        return {
            "status": "error",
            "diagnostics": validation["errors"],
            "machine_rankings": [],
            "findings": [],
        }
    machine_lookup, jobs, operations = snapshot_lookup(data)
    metrics = compute_schedule_metrics(data, assignments)
    simulation_by_machine = {
        item["machine_id"]: item
        for item in (simulation_metrics or {}).get("machine_metrics", [])
        if isinstance(item, dict) and "machine_id" in item
    }
    late_job_ids = {item["job_id"] for item in metrics["job_metrics"] if not item["on_time"]}
    machine_late_jobs = {mid: set() for mid in machine_lookup}
    operation_counts = {mid: 0 for mid in machine_lookup}
    for assignment in assignments:
        mid = assignment["machine_id"]
        operation_counts[mid] += 1
        if assignment["job_id"] in late_job_ids:
            machine_late_jobs[mid].add(assignment["job_id"])
    rankings = []
    for item in metrics["machine_metrics"]:
        mid = item["machine_id"]
        observed = simulation_by_machine.get(mid, {})
        rankings.append(
            {
                **item,
                "scheduled_operation_count": operation_counts[mid],
                "late_jobs_using_machine": len(machine_late_jobs[mid]),
                "mean_queue_wait_minutes": observed.get("mean_queue_wait_minutes"),
                "max_queue_length": observed.get("max_queue_length"),
            }
        )
    rankings.sort(
        key=lambda item: (
            item["utilization"] is None,
            -(item["utilization"] or 0),
            -item["busy_minutes"],
            item["machine_code"],
        )
    )

    findings: list[dict[str, Any]] = []
    if rankings:
        highest = next((item for item in rankings if item["utilization"] is not None), None)
        if highest:
            findings.append(
                {
                    "type": "capacity_pressure",
                    "machine_id": highest["machine_id"],
                    "title": f"{highest['machine_code']} has the highest scheduled utilization",
                    "evidence": {
                        "busy_minutes": highest["busy_minutes"],
                        "available_minutes": highest["available_minutes"],
                        "utilization": highest["utilization"],
                    },
                    "interpretation": "Utilization alone does not establish that this resource limits plant throughput. Compare a capacity scenario to test sensitivity.",
                }
            )
    queue_candidates = [item for item in rankings if item["mean_queue_wait_minutes"] is not None]
    if queue_candidates:
        longest = max(queue_candidates, key=lambda item: item["mean_queue_wait_minutes"])
        if longest["mean_queue_wait_minutes"] > 0:
            findings.append(
                {
                    "type": "queue_delay",
                    "machine_id": longest["machine_id"],
                    "title": f"{longest['machine_code']} has the longest simulated queue wait",
                    "evidence": {
                        "mean_queue_wait_minutes": longest["mean_queue_wait_minutes"],
                        "max_queue_length": longest["max_queue_length"],
                    },
                    "interpretation": "This is measured from the selected simulation. It can differ from the schedule's utilization ranking.",
                }
            )
    if metrics["late_jobs"]:
        findings.append(
            {
                "type": "delivery_risk",
                "machine_id": None,
                "title": f"{metrics['late_jobs']} scheduled jobs finish after their due time",
                "evidence": {
                    "late_jobs": metrics["late_jobs"],
                    "total_tardiness_minutes": metrics["total_tardiness_minutes"],
                },
                "interpretation": "Delay is calculated from final operation completion and each job's due time.",
            }
        )
    if not findings:
        findings.append(
            {
                "type": "no_exception",
                "machine_id": None,
                "title": "No delivery exception in the selected schedule",
                "evidence": {"late_jobs": 0},
                "interpretation": "Capacity sensitivity still requires a controlled scenario comparison.",
            }
        )

    makespan = metrics["makespan_minutes"]
    work_center_counts: dict[str, int] = {}
    for assignment in assignments:
        center = str(machine_lookup[assignment["machine_id"]].get("work_center_id") or "unassigned")
        work_center_counts[center] = work_center_counts.get(center, 0) + 1
    work_center_throughput = [
        {
            "work_center_id": center,
            "completed_operations": count,
            "operations_per_hour": count * 60 / makespan if makespan else None,
        }
        for center, count in sorted(work_center_counts.items())
    ]
    return {
        "status": "completed",
        "diagnostics": [],
        "machine_rankings": rankings,
        "findings": findings,
        "work_center_throughput": work_center_throughput,
        "late_jobs": [item for item in metrics["job_metrics"] if not item["on_time"]],
        "definitions": {
            "utilization": "Busy production minutes divided by operating-calendar minutes after planned downtime, from schedule origin through makespan.",
            "operations_per_hour": "Completed operations divided by scheduled elapsed time; this is not finished-job throughput.",
        },
    }
