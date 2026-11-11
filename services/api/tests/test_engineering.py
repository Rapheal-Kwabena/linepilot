"""Focused invariants for the real scheduling and simulation engines."""

from __future__ import annotations

from copy import deepcopy
from threading import Event

import pytest

from app.engineering import (
    analyze_bottlenecks,
    compute_schedule_metrics,
    schedule,
    simulate,
    validate_schedule,
)


@pytest.fixture
def snapshot():
    return {
        "origin": "2026-01-05T08:00:00Z",
        "horizon_minutes": 180,
        "machines": [
            {
                "id": "m1",
                "code": "M-01",
                "work_center_id": "cut",
                "hourly_cost": 60,
                "availability": [[0, 70], [90, 180]],
                "downtime": [[25, 35]],
            },
            {
                "id": "m2",
                "code": "M-02",
                "work_center_id": "finish",
                "hourly_cost": 30,
                "availability": [[0, 180]],
                "downtime": [[60, 80]],
            },
        ],
        "jobs": [
            {
                "id": "j1",
                "job_number": "JOB-01",
                "release_min": 0,
                "due_min": 55,
                "priority": 3,
                "operations": [
                    {
                        "id": "o1",
                        "operation_number": 1,
                        "name": "Cut",
                        "options": [
                            {"machine_id": "m1", "processing_minutes": 20},
                            {"machine_id": "m2", "processing_minutes": 28},
                        ],
                    },
                    {
                        "id": "o2",
                        "operation_number": 2,
                        "name": "Finish",
                        "options": [{"machine_id": "m2", "processing_minutes": 20}],
                    },
                ],
            },
            {
                "id": "j2",
                "job_number": "JOB-02",
                "release_min": 10,
                "due_min": 65,
                "priority": 1,
                "operations": [
                    {
                        "id": "o3",
                        "operation_number": 1,
                        "name": "Cut",
                        "options": [{"machine_id": "m1", "processing_minutes": 20}],
                    },
                    {
                        "id": "o4",
                        "operation_number": 2,
                        "name": "Finish",
                        "options": [{"machine_id": "m2", "processing_minutes": 15}],
                    },
                ],
            },
        ],
    }


@pytest.mark.parametrize("algorithm", ["fcfs", "spt", "edd", "cp_sat"])
def test_generated_schedules_respect_every_constraint(snapshot, algorithm):
    result = schedule(snapshot, algorithm, time_limit_seconds=5)
    assert result["status"] in {"feasible", "optimal"}, result["diagnostics"]
    assert validate_schedule(snapshot, result["assignments"]) == {"valid": True, "errors": []}
    assert {item["operation_id"] for item in result["assignments"]} == {"o1", "o2", "o3", "o4"}
    assert result["metrics"]["scheduled_operations"] == 4
    if algorithm == "cp_sat":
        assert result["solver_status"] in {"OPTIMAL", "FEASIBLE"}


@pytest.mark.parametrize(
    "edit,expected",
    [
        (lambda rows: rows[0].update(machine_id="not-eligible"), "unknown machine"),
        (lambda rows: rows[0].update(end_min=rows[0]["end_min"] + 1), "incorrect machine-specific duration"),
        (lambda rows: rows[0].update(start_min=-1, end_min=19), "outside the bounded horizon"),
        (lambda rows: rows.append(deepcopy(rows[0])), "assigned more than once"),
    ],
)
def test_validator_rejects_invalid_assignments(snapshot, edit, expected):
    rows = deepcopy(schedule(snapshot, "fcfs")["assignments"])
    edit(rows)
    validation = validate_schedule(snapshot, rows)
    assert not validation["valid"]
    assert any(expected in error for error in validation["errors"])


def test_validator_rejects_downtime_overlap_and_precedence(snapshot):
    rows = deepcopy(schedule(snapshot, "fcfs")["assignments"])
    o3 = next(row for row in rows if row["operation_id"] == "o3")
    o3.update(start_min=20, end_min=40)
    assert any("calendar or downtime" in error for error in validate_schedule(snapshot, rows)["errors"])
    rows = deepcopy(schedule(snapshot, "fcfs")["assignments"])
    o1 = next(row for row in rows if row["operation_id"] == "o1")
    o2 = next(row for row in rows if row["operation_id"] == "o2")
    o2.update(start_min=o1["start_min"], end_min=o1["start_min"] + 20)
    assert any("predecessor" in error for error in validate_schedule(snapshot, rows)["errors"])


def test_infeasible_schedule_does_not_claim_success(snapshot):
    snapshot["machines"][0]["availability"] = [[0, 10]]
    snapshot["machines"][1]["availability"] = [[0, 10]]
    for algorithm in ("fcfs", "cp_sat"):
        result = schedule(snapshot, algorithm, time_limit_seconds=2)
        assert result["status"] == "infeasible"
        assert result["assignments"] == []
        assert result["diagnostics"]


def test_closed_calendar_gap_and_overnight_window_are_respected():
    # The API expands an overnight shift into UTC minute windows. The engine
    # receives only those windows and must never bridge the closed interval.
    data = {
        "origin": "2026-01-05T22:00:00Z",
        "horizon_minutes": 180,
        "machines": [{"id": "m", "code": "M", "availability": [[0, 60], [120, 180]], "downtime": []}],
        "jobs": [
            {
                "id": "j",
                "job_number": "J",
                "release_min": 50,
                "due_min": 180,
                "priority": 1,
                "operations": [
                    {
                        "id": "o",
                        "operation_number": 1,
                        "options": [{"machine_id": "m", "processing_minutes": 30}],
                    }
                ],
            }
        ],
    }
    for algorithm in ("fcfs", "cp_sat"):
        result = schedule(data, algorithm, time_limit_seconds=3)
        assert result["status"] in {"feasible", "optimal"}, result["diagnostics"]
        assert result["assignments"][0]["start_min"] >= 120
        assert validate_schedule(data, result["assignments"])["valid"]


def test_downtime_splits_otherwise_available_machine_window():
    data = {
        "origin": "2026-01-05T08:00:00Z",
        "horizon_minutes": 100,
        "machines": [{"id": "m", "code": "M", "availability": [[0, 100]], "downtime": [[20, 40]]}],
        "jobs": [
            {
                "id": "j",
                "job_number": "J",
                "release_min": 10,
                "due_min": 100,
                "priority": 1,
                "operations": [
                    {
                        "id": "o",
                        "operation_number": 1,
                        "options": [{"machine_id": "m", "processing_minutes": 25}],
                    }
                ],
            }
        ],
    }
    result = schedule(data, "fcfs")
    assert result["assignments"][0]["start_min"] == 40
    machine = result["metrics"]["machine_metrics"][0]
    assert machine["downtime_minutes"] == 20
    assert machine["available_minutes"] == 45


def test_cp_sat_timed_status_never_claims_unproven_optimality(snapshot):
    result = schedule(snapshot, "cp_sat", time_limit_seconds=0.001)
    if result["status"] == "optimal":
        assert result["solver_status"] == "OPTIMAL"
    elif result["status"] == "feasible":
        assert result["solver_status"] == "FEASIBLE"
        assert any("not proven" in message for message in result["diagnostics"])
    else:
        assert result["status"] == "unknown"
        assert result["assignments"] == []


@pytest.mark.parametrize("workers", [0, 9, -1, True, 1.5, "2"])
def test_cp_sat_rejects_invalid_search_worker_count(snapshot, workers):
    result = schedule(snapshot, "cp_sat", search_workers=workers)
    assert result["status"] == "error"
    assert result["assignments"] == []
    assert any("search_workers must be an integer from 1 through 8" in item for item in result["diagnostics"])


def test_cp_sat_applies_solver_configuration_to_real_solver(snapshot, monkeypatch):
    from ortools.sat.python import cp_model

    actual_solver = cp_model.CpSolver
    created_solvers = []

    def capture_solver():
        solver = actual_solver()
        created_solvers.append(solver)
        return solver

    monkeypatch.setattr(cp_model, "CpSolver", capture_solver)
    result = schedule(snapshot, "cp_sat", time_limit_seconds=4, random_seed=77, search_workers=2)
    assert result["status"] in {"optimal", "feasible"}, result["diagnostics"]
    assert len(created_solvers) == 1
    parameters = created_solvers[0].parameters
    assert parameters.max_time_in_seconds == 4
    assert parameters.random_seed == 77
    assert parameters.num_search_workers == 2


def test_metric_formulas_and_findings_use_assignments(snapshot):
    result = schedule(snapshot, "fcfs")
    metrics = compute_schedule_metrics(snapshot, result["assignments"])
    assert metrics["makespan_minutes"] == max(row["end_min"] for row in result["assignments"])
    assert metrics["total_processing_minutes"] == sum(
        row["end_min"] - row["start_min"] for row in result["assignments"]
    )
    assert metrics["operating_cost"] == pytest.approx(
        sum(row["operating_cost"] for row in metrics["machine_metrics"])
    )
    assert metrics["total_tardiness_minutes"] == sum(
        row["tardiness_minutes"] for row in metrics["job_metrics"]
    )
    findings = analyze_bottlenecks(snapshot, result["assignments"])
    assert findings["status"] == "completed"
    assert findings["machine_rankings"][0]["utilization"] >= findings["machine_rankings"][1]["utilization"]


def test_deterministic_simulation_replays_stored_start_and_end(snapshot):
    planned = schedule(snapshot, "fcfs")
    assignments = planned["assignments"]
    result = simulate(
        snapshot, assignments, {"mode": "playback", "random_seed": 123, "horizon_minutes": None}
    )
    assert result["status"] == "completed", result["diagnostics"]
    assert result["mode"] == "deterministic"
    assert result["metrics"]["completed_jobs"] == len(snapshot["jobs"])
    starts = {
        event["operation_id"]: event["time_min"]
        for event in result["events"]
        if event["event_type"] == "processing_started"
    }
    ends = {
        event["operation_id"]: event["time_min"]
        for event in result["events"]
        if event["event_type"] == "processing_completed"
    }
    for row in assignments:
        assert starts[row["operation_id"]] == row["start_min"]
        assert ends[row["operation_id"]] == row["end_min"]
    assert result["metrics"]["mean_job_waiting_minutes"] == planned["metrics"]["mean_job_waiting_minutes"]
    assert len({event["job_id"] for event in result["events"] if event["event_type"] == "job_completed"}) == 2
    for row in assignments:
        if row["machine_id"] == "m1":
            assert not (starts[row["operation_id"]] < 35 and ends[row["operation_id"]] > 25)


def test_stochastic_simulation_is_seeded_and_reports_replications(snapshot):
    assignments = schedule(snapshot, "fcfs")["assignments"]
    config = {
        "mode": "stochastic",
        "random_seed": 17,
        "replications": 3,
        "processing_time_distribution": "triangular",
        "processing_time_variability": 0.5,
    }
    first = simulate(snapshot, assignments, config)
    second = simulate(snapshot, assignments, config)
    assert first["status"] == "completed", first["diagnostics"]
    assert first == second
    assert len(first["replication_metrics"]) == 3
    assert "completed_jobs" in first["confidence_intervals"]
    assert any(event["event_type"] == "queue_entered" for event in first["events"])
    fixed = simulate(
        snapshot,
        assignments,
        {
            "mode": "stochastic",
            "random_seed": 17,
            "processing_time_distribution": "fixed",
            "processing_time_variability": 0,
        },
    )
    assert fixed["status"] == "completed"
    assert (
        first["replication_metrics"][0]["total_processing_minutes"]
        != fixed["metrics"]["total_processing_minutes"]
    )


def test_cancellation_is_reported_without_result(snapshot):
    cancelled = Event()
    cancelled.set()
    for algorithm in ("fcfs", "cp_sat"):
        result = schedule(snapshot, algorithm, cancel_event=cancelled)
        assert result["status"] == "cancelled"
        assert result["assignments"] == []
