"""SimPy discrete-event production playback and stochastic experiments."""

from __future__ import annotations

import math
import random
import statistics
from typing import Any

from .common import (
    EngineeringInputError,
    clipped_length,
    earliest_fit,
    merge_intervals,
    normalize_snapshot,
    snapshot_lookup,
    subtract_intervals,
)
from .scheduling import validate_schedule


def _configuration(data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise EngineeringInputError("simulation configuration must be an object")
    mode = str(config.get("mode", "deterministic")).lower()
    if mode == "playback":
        mode = "deterministic"
    if mode not in {"deterministic", "stochastic"}:
        raise EngineeringInputError("simulation mode must be deterministic or stochastic")
    seed = config.get("random_seed", 0)
    replications = config.get("replications", 1)
    horizon = config.get("horizon_minutes")
    if horizon is None:
        horizon = data["horizon_minutes"]
    if any(isinstance(value, bool) or not isinstance(value, int) for value in (seed, replications, horizon)):
        raise EngineeringInputError("random_seed, replications, and horizon_minutes must be integers")
    if not 1 <= replications <= 100:
        raise EngineeringInputError("replications must be between 1 and 100")
    if not 1 <= horizon <= data["horizon_minutes"]:
        raise EngineeringInputError("simulation horizon must fit inside the snapshot horizon")
    distribution = str(config.get("processing_time_distribution", "triangular")).lower()
    if distribution not in {"fixed", "triangular", "lognormal"}:
        raise EngineeringInputError("unsupported processing time distribution")
    variability = config.get("processing_time_variability", 0.15)
    failure_rate = config.get("failure_rate_per_hour", 0.0)
    repair = config.get("mean_repair_minutes", 30.0)
    for name, value in (
        ("processing_time_variability", variability),
        ("failure_rate_per_hour", failure_rate),
        ("mean_repair_minutes", repair),
    ):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise EngineeringInputError(f"{name} must be finite and nonnegative")
    if variability > 1:
        raise EngineeringInputError("processing_time_variability must be at most 1")
    if failure_rate > 0 and repair <= 0:
        raise EngineeringInputError("mean_repair_minutes must be positive when failures are enabled")
    if mode == "deterministic":
        replications = 1
        distribution = "fixed"
        variability = 0.0
        failure_rate = 0.0
    return {
        "mode": mode,
        "random_seed": seed,
        "replications": replications,
        "horizon_minutes": horizon,
        "processing_time_distribution": distribution,
        "processing_time_variability": float(variability),
        "failure_rate_per_hour": float(failure_rate),
        "mean_repair_minutes": float(repair),
    }


def _draw_duration(base: int, settings: dict[str, Any], rng: random.Random) -> int:
    cv = settings["processing_time_variability"]
    distribution = settings["processing_time_distribution"]
    if distribution == "fixed" or cv == 0:
        return base
    if distribution == "triangular":
        return max(1, round(rng.triangular(max(1, base * (1 - cv)), base * (1 + cv), base)))
    sigma = math.sqrt(math.log(1 + cv * cv))
    mu = math.log(base) - sigma * sigma / 2
    return max(1, round(rng.lognormvariate(mu, sigma)))


def _failure_windows(horizon: int, settings: dict[str, Any], rng: random.Random) -> list[tuple[int, int]]:
    rate = settings["failure_rate_per_hour"]
    if not rate:
        return []
    current = 0.0
    windows: list[tuple[int, int]] = []
    # This bound prevents pathological input from producing unbounded events.
    for _ in range(1000):
        current += rng.expovariate(rate / 60)
        if current >= horizon:
            break
        start = max(0, min(horizon - 1, round(current)))
        duration = max(1, round(rng.expovariate(1 / settings["mean_repair_minutes"])))
        end = min(horizon, start + duration)
        windows.append((start, end))
        current = float(end)
    return merge_intervals(windows)


def _replication_metrics(
    data: dict[str, Any],
    events: list[dict[str, Any]],
    free_windows: dict[str, list[tuple[int, int]]],
    downtime_windows: dict[str, list[tuple[int, int]]],
    horizon: int,
) -> dict[str, Any]:
    machines, _, operations = snapshot_lookup(data)
    starts = {
        event["operation_id"]: event["time_min"]
        for event in events
        if event["event_type"] == "processing_started"
    }
    ends = {
        event["operation_id"]: event["time_min"]
        for event in events
        if event["event_type"] == "processing_completed"
    }
    completions = {
        event["job_id"]: event["time_min"] for event in events if event["event_type"] == "job_completed"
    }
    unfinished = len(completions) < len(data["jobs"])
    observation_end = horizon if unfinished else max(completions.values(), default=0)
    machine_busy = {mid: 0 for mid in machines}
    for op_id, end in ends.items():
        start = starts[op_id]
        machine_id = next(
            event["machine_id"]
            for event in events
            if event["event_type"] == "processing_completed" and event["operation_id"] == op_id
        )
        machine_busy[machine_id] += end - start
    machine_metrics = []
    queue_waits: dict[str, list[int]] = {mid: [] for mid in machines}
    queue_entries: dict[str, int] = {}
    max_queue: dict[str, int] = {mid: 0 for mid in machines}
    for event in events:
        mid = event["machine_id"]
        if event["event_type"] == "queue_entered":
            queue_entries[event["operation_id"]] = event["time_min"]
            max_queue[mid] = max(max_queue[mid], int(event["payload"].get("queue_length", 0)))
        elif event["event_type"] == "processing_started" and event["operation_id"] in queue_entries:
            queue_waits[mid].append(event["time_min"] - queue_entries[event["operation_id"]])
    for machine in data["machines"]:
        mid = machine["id"]
        available = clipped_length(free_windows[mid], 0, observation_end)
        scheduled = clipped_length([(a, b) for a, b in machine["availability"]], 0, observation_end)
        busy = machine_busy[mid]
        machine_metrics.append(
            {
                "machine_id": mid,
                "machine_code": machine["code"],
                "work_center_id": machine.get("work_center_id"),
                "busy_minutes": busy,
                "available_minutes": available,
                "idle_minutes": max(0, available - busy),
                "downtime_minutes": max(0, scheduled - available),
                "utilization": busy / available if available else None,
                "mean_queue_wait_minutes": statistics.mean(queue_waits[mid]) if queue_waits[mid] else None,
                "max_queue_length": max_queue[mid],
                "operating_cost": busy * machine["hourly_cost"] / 60,
            }
        )
    job_metrics = []
    for job in data["jobs"]:
        completion = completions.get(job["id"])
        if completion is None:
            continue
        processing = sum(ends[op["id"]] - starts[op["id"]] for op in job["operations"])
        flow = completion - job["release_min"]
        tardiness = max(0, completion - job["due_min"])
        job_metrics.append(
            {
                "job_id": job["id"],
                "job_number": job["job_number"],
                "completion_min": completion,
                "flow_minutes": flow,
                "waiting_minutes": max(0, flow - processing),
                "processing_minutes": processing,
                "tardiness_minutes": tardiness,
                "on_time": tardiness == 0,
            }
        )
    wip = 0
    wip_series = []
    for event in events:
        if event["event_type"] == "job_released":
            wip += 1
            wip_series.append({"time_min": event["time_min"], "wip": wip})
        elif event["event_type"] == "job_completed":
            wip -= 1
            wip_series.append({"time_min": event["time_min"], "wip": wip})
    completed = len(job_metrics)
    on_time = sum(item["on_time"] for item in job_metrics)
    utilization_values = [item["utilization"] for item in machine_metrics if item["utilization"] is not None]
    return {
        "observation_start_min": 0,
        "observation_end_min": observation_end,
        "throughput_jobs_per_hour": completed * 60 / observation_end if observation_end else None,
        "completed_jobs": completed,
        "unfinished_jobs": len(data["jobs"]) - completed,
        "late_jobs": completed - on_time,
        "on_time_delivery_rate": on_time / completed if completed else None,
        "mean_job_flow_minutes": statistics.mean(item["flow_minutes"] for item in job_metrics)
        if job_metrics
        else None,
        "mean_job_waiting_minutes": statistics.mean(item["waiting_minutes"] for item in job_metrics)
        if job_metrics
        else None,
        "average_machine_utilization": statistics.mean(utilization_values) if utilization_values else None,
        "total_processing_minutes": sum(machine_busy.values()),
        "operating_cost": sum(item["operating_cost"] for item in machine_metrics),
        "max_wip": max((item["wip"] for item in wip_series), default=0),
        "wip_time_series": wip_series,
        "machine_metrics": machine_metrics,
        "job_metrics": job_metrics,
    }


def _run_once(
    data: dict[str, Any],
    assignments: list[dict[str, Any]],
    settings: dict[str, Any],
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        import simpy
    except ImportError as exc:
        raise EngineeringInputError(
            "SimPy is not installed; install the API requirements to run simulation"
        ) from exc

    env = simpy.Environment()
    rng = random.Random(seed)
    horizon = settings["horizon_minutes"]
    machines, _, _ = snapshot_lookup(data)
    by_operation = {item["operation_id"]: item for item in assignments}
    queue_lengths = {mid: 0 for mid in machines}
    events: list[dict[str, Any]] = []

    def record(
        event_type: str,
        job_id: str | None = None,
        operation_id: str | None = None,
        machine_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        events.append(
            {
                "time_min": int(env.now),
                "event_type": event_type,
                "job_id": job_id,
                "operation_id": operation_id,
                "machine_id": machine_id,
                "payload": payload or {},
            }
        )

    downtime_windows: dict[str, list[tuple[int, int]]] = {}
    free_windows: dict[str, list[tuple[int, int]]] = {}
    for mid, machine in machines.items():
        extra = _failure_windows(horizon, settings, rng) if settings["mode"] == "stochastic" else []
        downtime = merge_intervals([(a, b) for a, b in machine["downtime"]] + extra)
        downtime_windows[mid] = downtime
        free_windows[mid] = subtract_intervals([(a, b) for a, b in machine["availability"]], downtime)

        def downtime_events(machine_id: str, windows: list[tuple[int, int]]):
            for start, end in windows:
                yield env.timeout(max(0, start - env.now))
                record("machine_down", machine_id=machine_id, payload={"end_min": end})
                yield env.timeout(max(0, end - env.now))
                record("machine_available", machine_id=machine_id)

        env.process(downtime_events(mid, downtime))

    if settings["mode"] == "deterministic":

        def replay_job(job: dict[str, Any]):
            yield env.timeout(max(0, job["release_min"]))
            record("job_released", job_id=job["id"], payload={"job_number": job["job_number"]})
            for operation in job["operations"]:
                assignment = by_operation[operation["id"]]
                mid = assignment["machine_id"]
                queue_lengths[mid] += 1
                record(
                    "queue_entered",
                    job_id=job["id"],
                    operation_id=operation["id"],
                    machine_id=mid,
                    payload={"queue_length": queue_lengths[mid]},
                )
                yield env.timeout(max(0, assignment["start_min"] - env.now))
                queue_lengths[mid] -= 1
                record(
                    "processing_started",
                    job_id=job["id"],
                    operation_id=operation["id"],
                    machine_id=mid,
                    payload={
                        "queue_length": queue_lengths[mid],
                        "scheduled_start_min": assignment["start_min"],
                    },
                )
                yield env.timeout(assignment["end_min"] - assignment["start_min"])
                record(
                    "processing_completed",
                    job_id=job["id"],
                    operation_id=operation["id"],
                    machine_id=mid,
                    payload={"duration_minutes": assignment["end_min"] - assignment["start_min"]},
                )
            record("job_completed", job_id=job["id"], payload={"job_number": job["job_number"]})

        for job in data["jobs"]:
            env.process(replay_job(job))
    else:
        resources = {mid: simpy.Resource(env, capacity=1) for mid in machines}

        def stochastic_job(job: dict[str, Any]):
            yield env.timeout(max(0, job["release_min"]))
            record("job_released", job_id=job["id"], payload={"job_number": job["job_number"]})
            for operation in job["operations"]:
                assignment = by_operation[operation["id"]]
                mid = assignment["machine_id"]
                base_duration = next(
                    option["processing_minutes"]
                    for option in operation["options"]
                    if option["machine_id"] == mid
                )
                duration = _draw_duration(base_duration, settings, rng)
                queue_lengths[mid] += 1
                record(
                    "queue_entered",
                    job_id=job["id"],
                    operation_id=operation["id"],
                    machine_id=mid,
                    payload={"queue_length": queue_lengths[mid]},
                )
                with resources[mid].request() as request:
                    yield request
                    start = earliest_fit(free_windows[mid], [], int(env.now), duration)
                    if start is None or start + duration > horizon:
                        queue_lengths[mid] -= 1
                        record(
                            "operation_unfinished",
                            job_id=job["id"],
                            operation_id=operation["id"],
                            machine_id=mid,
                            payload={
                                "reason": "No continuous machine window remains within the simulation horizon"
                            },
                        )
                        record("job_unfinished", job_id=job["id"], payload={"job_number": job["job_number"]})
                        return
                    yield env.timeout(max(0, start - env.now))
                    queue_lengths[mid] -= 1
                    record(
                        "processing_started",
                        job_id=job["id"],
                        operation_id=operation["id"],
                        machine_id=mid,
                        payload={"queue_length": queue_lengths[mid], "sampled_duration_minutes": duration},
                    )
                    yield env.timeout(duration)
                    record(
                        "processing_completed",
                        job_id=job["id"],
                        operation_id=operation["id"],
                        machine_id=mid,
                        payload={"duration_minutes": duration},
                    )
            record("job_completed", job_id=job["id"], payload={"job_number": job["job_number"]})

        for job in data["jobs"]:
            env.process(stochastic_job(job))
    run_until = (
        max((item["end_min"] for item in assignments), default=0) + 1
        if settings["mode"] == "deterministic"
        else horizon + 1
    )
    env.run(until=run_until)
    completed_job_ids = {event["job_id"] for event in events if event["event_type"] == "job_completed"}
    already_unfinished = {event["job_id"] for event in events if event["event_type"] == "job_unfinished"}
    if settings["mode"] == "stochastic":
        for job in data["jobs"]:
            if job["id"] not in completed_job_ids and job["id"] not in already_unfinished:
                events.append(
                    {
                        "time_min": horizon,
                        "event_type": "job_unfinished",
                        "job_id": job["id"],
                        "operation_id": None,
                        "machine_id": None,
                        "payload": {"job_number": job["job_number"], "reason": "Simulation horizon reached"},
                    }
                )
    events.sort(key=lambda event: event["time_min"])
    return events, _replication_metrics(data, events, free_windows, downtime_windows, horizon)


def simulate(
    snapshot: dict[str, Any], assignments: list[dict[str, Any]], config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Replay a validated schedule or run seeded stochastic SimPy replications."""
    response: dict[str, Any] = {
        "status": "error",
        "mode": None,
        "seed": None,
        "replications": 0,
        "events": [],
        "metrics": {},
        "replication_metrics": [],
        "confidence_intervals": {},
        "diagnostics": [],
    }
    try:
        data = normalize_snapshot(snapshot)
        validation = validate_schedule(data, assignments)
        if not validation["valid"]:
            raise EngineeringInputError("schedule is invalid: " + "; ".join(validation["errors"][:5]))
        settings = _configuration(data, config or {})
        if settings["mode"] == "deterministic" and any(
            item["end_min"] > settings["horizon_minutes"] for item in assignments
        ):
            raise EngineeringInputError("deterministic simulation horizon ends before the stored schedule")
        response.update(
            mode=settings["mode"], seed=settings["random_seed"], replications=settings["replications"]
        )
        replications: list[dict[str, Any]] = []
        for index in range(settings["replications"]):
            events, metrics = _run_once(data, assignments, settings, settings["random_seed"] + index)
            replications.append(metrics)
            if index == 0:
                response["events"] = events
        # The first replication is the inspectable event stream. Aggregate
        # scalar metrics are means across all replications; nested details are
        # retained from that first stream to avoid implying a pooled timeline.
        first = dict(replications[0])
        intervals: dict[str, dict[str, float]] = {}
        for key, value in first.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            samples = [item[key] for item in replications if isinstance(item.get(key), (int, float))]
            if samples:
                first[key] = statistics.mean(samples)
                if len(samples) > 1:
                    mean = statistics.mean(samples)
                    half_width = 1.96 * statistics.stdev(samples) / math.sqrt(len(samples))
                    intervals[key] = {
                        "lower": mean - half_width,
                        "upper": mean + half_width,
                        "method": "normal_approximation_95_percent",
                    }
        response.update(
            status="completed",
            metrics=first,
            replication_metrics=replications,
            confidence_intervals=intervals,
        )
    except (EngineeringInputError, ValueError, RuntimeError) as exc:
        response["diagnostics"] = [str(exc)]
    return response
