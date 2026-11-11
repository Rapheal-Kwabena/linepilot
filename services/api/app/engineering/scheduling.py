"""Feasible dispatch rules, CP-SAT optimization, and independent validation."""

from __future__ import annotations

import math
import threading
import time
from typing import Any

from .common import (
    EngineeringInputError,
    complement_intervals,
    earliest_fit,
    interval_fits,
    normalize_snapshot,
    snapshot_lookup,
)
from .metrics import compute_schedule_metrics


class _Cancelled(Exception):
    pass


def _cancelled(cancel_event: Any) -> bool:
    return bool(cancel_event is not None and cancel_event.is_set())


def validate_schedule(snapshot: dict[str, Any], assignments: list[dict[str, Any]]) -> dict[str, Any]:
    """Independently enforce every critical scheduling invariant.

    Validation does not call the dispatch or CP-SAT solver. It is run before a
    generated schedule is returned for persistence and can also check imports.
    """
    try:
        data = normalize_snapshot(snapshot)
    except EngineeringInputError as exc:
        return {"valid": False, "errors": [str(exc)]}
    if not isinstance(assignments, list):
        return {"valid": False, "errors": ["assignments must be an array"]}
    machines, _, operations = snapshot_lookup(data)
    horizon = data["horizon_minutes"]
    errors: list[str] = []
    by_operation: dict[str, dict[str, Any]] = {}
    by_machine: dict[str, list[tuple[int, int, str]]] = {mid: [] for mid in machines}

    for index, assignment in enumerate(assignments):
        if not isinstance(assignment, dict):
            errors.append(f"assignment {index} is not an object")
            continue
        op_id = str(assignment.get("operation_id") or "")
        if op_id not in operations:
            errors.append(f"assignment {index} names unknown operation {op_id}")
            continue
        if op_id in by_operation:
            errors.append(f"operation {op_id} is assigned more than once")
            continue
        by_operation[op_id] = assignment
        job, operation = operations[op_id]
        if str(assignment.get("job_id")) != job["id"]:
            errors.append(f"operation {op_id} has incorrect job_id")
        machine_id = str(assignment.get("machine_id") or "")
        if machine_id not in machines:
            errors.append(f"operation {op_id} has unknown machine {machine_id}")
            continue
        options = {option["machine_id"]: option for option in operation["options"]}
        if machine_id not in options:
            errors.append(f"operation {op_id} is assigned to ineligible machine {machine_id}")
        start = assignment.get("start_min")
        end = assignment.get("end_min")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
        ):
            errors.append(f"operation {op_id} times must be integer minutes")
            continue
        if start < 0 or end > horizon or end <= start:
            errors.append(f"operation {op_id} lies outside the bounded horizon")
        if start < job["release_min"]:
            errors.append(f"operation {op_id} begins before job release")
        if machine_id in options and end - start != options[machine_id]["processing_minutes"]:
            errors.append(f"operation {op_id} has incorrect machine-specific duration")
        if not interval_fits(machines[machine_id]["free_windows"], start, end):
            errors.append(f"operation {op_id} conflicts with calendar or downtime on {machine_id}")
        by_machine[machine_id].append((start, end, op_id))

    for op_id in operations:
        if op_id not in by_operation:
            errors.append(f"operation {op_id} is missing")
    for job in data["jobs"]:
        for predecessor, successor in zip(job["operations"], job["operations"][1:], strict=False):
            left = by_operation.get(predecessor["id"])
            right = by_operation.get(successor["id"])
            if (
                left
                and right
                and isinstance(left.get("end_min"), int)
                and isinstance(right.get("start_min"), int)
            ):
                if right["start_min"] < left["end_min"]:
                    errors.append(
                        f"operation {successor['id']} begins before predecessor {predecessor['id']} ends"
                    )
    for machine_id, spans in by_machine.items():
        spans.sort(key=lambda span: (span[0], span[1], span[2]))
        for previous, current in zip(spans, spans[1:], strict=False):
            if current[0] < previous[1]:
                errors.append(f"machine {machine_id} overlaps operations {previous[2]} and {current[2]}")
    return {"valid": not errors, "errors": errors}


def _dispatch(
    data: dict[str, Any], algorithm: str, cancel_event: Any
) -> tuple[str, list[dict[str, Any]], list[str]]:
    machines, _, _ = snapshot_lookup(data)
    occupied: dict[str, list[tuple[int, int]]] = {mid: [] for mid in machines}
    next_index = {job["id"]: 0 for job in data["jobs"]}
    ready = {job["id"]: max(0, job["release_min"]) for job in data["jobs"]}
    total_operations = sum(len(job["operations"]) for job in data["jobs"])
    assignments: list[dict[str, Any]] = []

    for _ in range(total_operations):
        if _cancelled(cancel_event):
            raise _Cancelled
        candidates: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, Any], int, int]] = []
        no_slot: list[str] = []
        for job in data["jobs"]:
            index = next_index[job["id"]]
            if index >= len(job["operations"]):
                continue
            operation = job["operations"][index]
            options: list[tuple[int, int, str, int]] = []
            for option in operation["options"]:
                mid = option["machine_id"]
                duration = option["processing_minutes"]
                start = earliest_fit(machines[mid]["free_windows"], occupied[mid], ready[job["id"]], duration)
                if start is not None:
                    options.append((start, start + duration, mid, duration))
            if not options:
                no_slot.append(f"{job['job_number']} operation {operation['operation_number']}")
                continue
            start, end, mid, duration = min(
                options, key=lambda value: (value[0], value[1], machines[value[2]]["code"])
            )
            if algorithm == "fcfs":
                priority_key: tuple[Any, ...] = (job["release_min"], job["job_number"])
            elif algorithm == "spt":
                priority_key = (duration, job["release_min"], job["job_number"])
            else:  # EDD
                priority_key = (job["due_min"], job["release_min"], job["job_number"])
            # Dispatch among operations able to start at the earliest event.
            rank = (start, priority_key, end, machines[mid]["code"])
            candidates.append((rank, job, operation, start, end))
        if not candidates:
            return (
                "infeasible",
                [],
                ["No remaining operation fits the configured machine windows within the horizon."]
                + (["Unplaced: " + ", ".join(no_slot[:8])] if no_slot else []),
            )
        _, job, operation, start, end = min(candidates, key=lambda entry: entry[0])
        # Re-read the chosen machine from its ranked candidate. Ties are broken
        # by option completion time and machine code above.
        options = []
        for option in operation["options"]:
            mid = option["machine_id"]
            duration = option["processing_minutes"]
            slot = earliest_fit(machines[mid]["free_windows"], occupied[mid], ready[job["id"]], duration)
            if slot is not None:
                options.append((slot, slot + duration, mid))
        start, end, mid = min(options, key=lambda value: (value[0], value[1], machines[value[2]]["code"]))
        assignments.append(
            {
                "operation_id": operation["id"],
                "job_id": job["id"],
                "machine_id": mid,
                "start_min": start,
                "end_min": end,
            }
        )
        occupied[mid].append((start, end))
        ready[job["id"]] = end
        next_index[job["id"]] += 1
    return "feasible", assignments, []


def _cp_sat(
    data: dict[str, Any],
    objective: str,
    time_limit_seconds: float,
    random_seed: int,
    cancel_event: Any,
    search_workers: int,
) -> tuple[str, str, list[dict[str, Any]], list[str], float | None]:
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:
        raise EngineeringInputError(
            "OR-Tools is not installed; install the API requirements to run CP-SAT"
        ) from exc
    if _cancelled(cancel_event):
        raise _Cancelled
    # A valid dispatch schedule is a useful upper bound and a complete search
    # hint. It prevents a short optimization run from wasting its first feasible
    # incumbent on a schedule far worse than ordinary dispatch.
    warm_starts: list[tuple[int, list[dict[str, Any]], str]] = []
    for rule in ("fcfs", "spt", "edd"):
        baseline_status, baseline_rows, _ = _dispatch(data, rule, cancel_event)
        if baseline_status != "feasible":
            continue
        by_op = {row["operation_id"]: row for row in baseline_rows}
        makespan_value = max((row["end_min"] for row in baseline_rows), default=0)
        tardiness_value = sum(
            max(0, by_op[job["operations"][-1]["id"]]["end_min"] - job["due_min"]) for job in data["jobs"]
        )
        weighted_value = sum(
            job["priority"] * max(0, by_op[job["operations"][-1]["id"]]["end_min"] - job["due_min"])
            for job in data["jobs"]
        )
        score = {
            "makespan": makespan_value,
            "total_tardiness": tardiness_value,
            "weighted_tardiness": weighted_value,
            "balanced": max(1, len(data["jobs"])) * makespan_value + weighted_value,
            "combined": max(1, len(data["jobs"])) * makespan_value + weighted_value,
        }.get(objective)
        if score is not None:
            warm_starts.append((score, baseline_rows, rule))
    best_start = min(warm_starts, key=lambda entry: entry[0]) if warm_starts else None
    model = cp_model.CpModel()
    horizon = data["horizon_minutes"]
    machines, _, _ = snapshot_lookup(data)
    machine_intervals: dict[str, list[Any]] = {mid: [] for mid in machines}
    for mid, machine in machines.items():
        for index, (start, end) in enumerate(complement_intervals(machine["free_windows"], horizon)):
            machine_intervals[mid].append(
                model.NewIntervalVar(start, end - start, end, f"blocked_{mid}_{index}")
            )

    master_start: dict[str, Any] = {}
    master_end: dict[str, Any] = {}
    choices: dict[str, list[tuple[str, Any, Any, Any]]] = {}
    for job in data["jobs"]:
        previous_end: Any = None
        for operation in job["operations"]:
            if _cancelled(cancel_event):
                raise _Cancelled
            op_id = operation["id"]
            start_var = model.NewIntVar(0, horizon, f"start_{op_id}")
            end_var = model.NewIntVar(0, horizon, f"end_{op_id}")
            master_start[op_id] = start_var
            master_end[op_id] = end_var
            model.Add(start_var >= max(0, job["release_min"]))
            if previous_end is not None:
                model.Add(start_var >= previous_end)
            previous_end = end_var
            op_choices: list[tuple[str, Any, Any, Any]] = []
            for option in operation["options"]:
                mid = option["machine_id"]
                duration = option["processing_minutes"]
                if not any(end - start >= duration for start, end in machines[mid]["free_windows"]):
                    continue
                presence = model.NewBoolVar(f"use_{op_id}_{mid}")
                chosen_start = model.NewIntVar(0, horizon - duration, f"selected_start_{op_id}_{mid}")
                chosen_end = model.NewIntVar(duration, horizon, f"selected_end_{op_id}_{mid}")
                model.Add(chosen_end == chosen_start + duration).OnlyEnforceIf(presence)
                model.Add(start_var == chosen_start).OnlyEnforceIf(presence)
                model.Add(end_var == chosen_end).OnlyEnforceIf(presence)
                interval = model.NewOptionalIntervalVar(
                    chosen_start, duration, chosen_end, presence, f"op_{op_id}_{mid}"
                )
                machine_intervals[mid].append(interval)
                op_choices.append((mid, presence, chosen_start, chosen_end))
            if not op_choices:
                return (
                    "infeasible",
                    "INFEASIBLE",
                    [],
                    [f"Operation {op_id} has no machine window long enough for its non-preemptive duration."],
                    None,
                )
            model.AddExactlyOne([choice[1] for choice in op_choices])
            choices[op_id] = op_choices
    for intervals in machine_intervals.values():
        if intervals:
            model.AddNoOverlap(intervals)

    job_completions = [master_end[job["operations"][-1]["id"]] for job in data["jobs"]]
    makespan = model.NewIntVar(0, horizon, "makespan")
    if job_completions:
        model.AddMaxEquality(makespan, job_completions)
    else:
        model.Add(makespan == 0)
    tardiness_terms = []
    tardiness_by_job: dict[str, Any] = {}
    weighted_terms = []
    for job in data["jobs"]:
        bound = horizon + max(0, -job["due_min"])
        tardiness = model.NewIntVar(0, bound, f"tardiness_{job['id']}")
        model.Add(tardiness >= master_end[job["operations"][-1]["id"]] - job["due_min"])
        tardiness_terms.append(tardiness)
        tardiness_by_job[job["id"]] = tardiness
        weighted_terms.append(job["priority"] * tardiness)
    if objective == "makespan":
        model.Minimize(makespan)
    elif objective == "total_tardiness":
        model.Minimize(sum(tardiness_terms))
    elif objective == "weighted_tardiness":
        model.Minimize(sum(weighted_terms))
    elif objective in ("balanced", "combined"):
        # Makespan plus mean priority-weighted tardiness, both in minutes.
        # Multiplication by n avoids fractional coefficients in CP-SAT.
        model.Minimize(max(1, len(data["jobs"])) * makespan + sum(weighted_terms))
    else:
        raise EngineeringInputError(f"unsupported optimization objective {objective}")

    if best_start is not None:
        best_score, warm_rows, _ = best_start
        if objective == "makespan":
            model.Add(makespan <= best_score)
        elif objective == "total_tardiness":
            model.Add(sum(tardiness_terms) <= best_score)
        elif objective == "weighted_tardiness":
            model.Add(sum(weighted_terms) <= best_score)
        else:
            model.Add(max(1, len(data["jobs"])) * makespan + sum(weighted_terms) <= best_score)
        warm_by_op = {row["operation_id"]: row for row in warm_rows}
        model.AddHint(makespan, max((row["end_min"] for row in warm_rows), default=0))
        for job in data["jobs"]:
            completion = warm_by_op[job["operations"][-1]["id"]]["end_min"]
            model.AddHint(tardiness_by_job[job["id"]], max(0, completion - job["due_min"]))
            for operation in job["operations"]:
                row = warm_by_op[operation["id"]]
                model.AddHint(master_start[operation["id"]], row["start_min"])
                model.AddHint(master_end[operation["id"]], row["end_min"])
                for mid, presence, chosen_start, chosen_end in choices[operation["id"]]:
                    selected = mid == row["machine_id"]
                    model.AddHint(presence, 1 if selected else 0)
                    if selected:
                        model.AddHint(chosen_start, row["start_min"])
                        model.AddHint(chosen_end, row["end_min"])

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.random_seed = random_seed
    solver.parameters.num_search_workers = search_workers
    done = threading.Event()

    def stop_when_cancelled() -> None:
        while not done.wait(0.05):
            if _cancelled(cancel_event):
                solver.StopSearch()
                return

    watcher = threading.Thread(target=stop_when_cancelled, daemon=True)
    if cancel_event is not None:
        watcher.start()
    try:
        status_code = solver.Solve(model)
    finally:
        done.set()
        if cancel_event is not None:
            watcher.join(timeout=0.2)
    solver_status = solver.StatusName(status_code).upper()
    if _cancelled(cancel_event):
        return "cancelled", solver_status, [], ["Optimization was cancelled."], None
    if status_code not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        status = "infeasible" if status_code == cp_model.INFEASIBLE else "unknown"
        diagnostic = (
            "No feasible schedule exists under the current model constraints. Check horizon, machine windows, and operation eligibility."
            if status == "infeasible"
            else "The solver did not find a feasible schedule within the configured time limit."
        )
        return status, solver_status, [], [diagnostic], None
    assignments: list[dict[str, Any]] = []
    for job in data["jobs"]:
        for operation in job["operations"]:
            for mid, presence, start_var, end_var in choices[operation["id"]]:
                if solver.Value(presence):
                    assignments.append(
                        {
                            "operation_id": operation["id"],
                            "job_id": job["id"],
                            "machine_id": mid,
                            "start_min": solver.Value(start_var),
                            "end_min": solver.Value(end_var),
                        }
                    )
                    break
    diagnostics = []
    if status_code == cp_model.FEASIBLE:
        diagnostics.append("A feasible schedule was found; optimality was not proven within the time limit.")
    return (
        "optimal" if status_code == cp_model.OPTIMAL else "feasible",
        solver_status,
        assignments,
        diagnostics,
        float(solver.ObjectiveValue()),
    )


def schedule(
    snapshot: dict[str, Any],
    algorithm: str = "fcfs",
    objective: str = "makespan",
    time_limit_seconds: float = 10.0,
    random_seed: int = 0,
    cancel_event: Any = None,
    search_workers: int = 1,
) -> dict[str, Any]:
    """Generate and validate a feasible resource-constrained schedule.

    FCFS, SPT and EDD are dispatch rules. CP-SAT is exact optimization when
    OR-Tools proves optimality; a timed result may be merely feasible.
    """
    started = time.perf_counter()
    response: dict[str, Any] = {
        "status": "error",
        "solver_status": "ERROR",
        "assignments": [],
        "metrics": {},
        "diagnostics": [],
        "objective_value": None,
        "runtime_ms": 0,
    }
    try:
        data = normalize_snapshot(snapshot)
        algorithm = algorithm.lower().replace("-", "_")
        if algorithm not in {"fcfs", "spt", "edd", "cp_sat"}:
            raise EngineeringInputError(f"unsupported scheduling algorithm {algorithm}")
        if (
            isinstance(time_limit_seconds, bool)
            or not isinstance(time_limit_seconds, (int, float))
            or not math.isfinite(time_limit_seconds)
            or time_limit_seconds <= 0
        ):
            raise EngineeringInputError("time_limit_seconds must be positive and finite")
        if isinstance(random_seed, bool) or not isinstance(random_seed, int):
            raise EngineeringInputError("random_seed must be an integer")
        if (
            isinstance(search_workers, bool)
            or not isinstance(search_workers, int)
            or not 1 <= search_workers <= 8
        ):
            raise EngineeringInputError("search_workers must be an integer from 1 through 8")
        if _cancelled(cancel_event):
            raise _Cancelled
        if algorithm == "cp_sat":
            status, solver_status, assignments, diagnostics, objective_value = _cp_sat(
                data,
                objective,
                min(float(time_limit_seconds), 300.0),
                random_seed,
                cancel_event,
                search_workers,
            )
        else:
            status, assignments, diagnostics = _dispatch(data, algorithm, cancel_event)
            solver_status = "DISPATCHED" if status == "feasible" else status.upper()
            objective_value = None
        response.update(
            status=status,
            solver_status=solver_status,
            assignments=assignments,
            diagnostics=diagnostics,
            objective_value=objective_value,
        )
        if status in {"optimal", "feasible"}:
            validation = validate_schedule(data, assignments)
            if not validation["valid"]:
                response.update(
                    status="error",
                    solver_status="INVALID_RESULT",
                    assignments=[],
                    diagnostics=["Generated schedule failed independent validation."] + validation["errors"],
                )
            else:
                response["metrics"] = compute_schedule_metrics(data, assignments)
    except _Cancelled:
        response.update(
            status="cancelled", solver_status="CANCELLED", diagnostics=["Scheduling was cancelled."]
        )
    except (EngineeringInputError, ValueError, RuntimeError) as exc:
        response["diagnostics"] = [str(exc)]
    finally:
        response["runtime_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return response
