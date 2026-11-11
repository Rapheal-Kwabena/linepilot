"""Reproduce the measured M-01 downtime experiment in docs/case-study.md.

Run from the repository root with:
    PYTHONPATH=services/api .venv/bin/python scripts/case_study_experiment.py

Only an isolated in-memory database is used. No saved workspace is changed.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.db import Base
from app.engineering import analyze_bottlenecks, schedule, simulate, validate_schedule
from app.seed import seed_facility
from app.snapshot import apply_modifications, build_snapshot


ANCHOR = date(2026, 11, 16)
FAILURE_START = "2026-11-16T14:00:00Z"
FAILURE_END = "2026-11-16T18:00:00Z"
EXPECTED = {
    "baseline": (1974, 483, 3, 64.35, "M-06"),
    "scenario": (1974, 529, 3, 70.35, "M-04"),
}


def measure(snapshot: dict) -> tuple[int, int, int, float, str]:
    result = schedule(snapshot, "fcfs")
    assert result["status"] == "feasible", result["diagnostics"]
    assert validate_schedule(snapshot, result["assignments"])["valid"]
    playback = simulate(snapshot, result["assignments"], {"mode": "playback", "random_seed": 0})
    assert playback["status"] == "completed", playback["diagnostics"]
    analysis = analyze_bottlenecks(snapshot, result["assignments"], playback["metrics"])
    assert analysis["status"] == "completed"
    metrics = result["metrics"]
    return (
        metrics["makespan_minutes"],
        metrics["total_tardiness_minutes"],
        metrics["late_jobs"],
        round(playback["metrics"]["mean_job_waiting_minutes"], 2),
        analysis["machine_rankings"][0]["machine_code"],
    )


def main() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            facility = seed_facility(db, anchor=ANCHOR)
            baseline = build_snapshot(db, facility, horizon_hours=336)
            assert baseline["origin"] == "2026-11-16T12:00:00Z", baseline["origin"]
            machine = next(item for item in baseline["machines"] if item["code"] == "M-01")
            scenario = apply_modifications(
                baseline,
                [{"type": "machine_failure", "machine_id": machine["id"],
                  "start_time": FAILURE_START, "end_time": FAILURE_END}],
                scenario_id="case-study",
            )
            print("Fictional Northline sample, 20 jobs, FCFS + deterministic SimPy playback")
            print("Case         Makespan  Tardiness  Late jobs  Mean waiting  Highest utilization")
            for label, snapshot in (("baseline", baseline), ("scenario", scenario)):
                values = measure(snapshot)
                print(f"{label:12} {values[0]:>7}m  {values[1]:>8} job-min  {values[2]:>3}  {values[3]:>8.2f}m  {values[4]}")
                assert values == EXPECTED[label], (
                    f"Published case-study values changed for {label}: expected {EXPECTED[label]}, got {values}. "
                    "Regenerate the experiment and update docs/case-study.md."
                )
            print("Published case-study values verified.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
