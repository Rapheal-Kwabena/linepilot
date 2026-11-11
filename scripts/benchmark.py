"""Run reproducible, measured scheduling benchmarks on fictional demo inputs.

Usage from repository root:
    PYTHONPATH=services/api .venv/bin/python scripts/benchmark.py

The script uses an isolated in-memory database. It never rewrites operational
data or supplies precomputed improvements. The Markdown file is regenerated
from actual engine outputs on each run.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import platform

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.db import Base
from app.engineering import schedule
from app.seed import seed_facility
from app.snapshot import build_snapshot


ROOT = Path(__file__).resolve().parents[1]


def _number(value: float | int | None, digits: int = 1) -> str:
    return "N/A" if value is None else f"{value:,.{digits}f}"


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def _row(label: str, objective: str, result: dict) -> str:
    metrics = result["metrics"]
    status = result["status"]
    if status == "feasible" and result["solver_status"] == "FEASIBLE":
        status = "Feasible (not proven optimal)"
    else:
        status = status.capitalize()
    return " | ".join(
        [
            label,
            objective,
            status,
            _number(metrics.get("makespan_minutes"), 0),
            _number(metrics.get("total_tardiness_minutes"), 0),
            _percent(metrics.get("on_time_delivery_rate")),
            _percent(metrics.get("average_machine_utilization")),
            _number(result["runtime_ms"], 1),
        ]
    ) + " |"


def run_benchmark(anchor: date, cp_sat_limit: float, horizon_hours: int) -> dict[str, dict]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    results: dict[str, dict] = {}
    try:
        with Session(engine) as db:
            for size, large in (("20 jobs", False), ("100 jobs", True)):
                facility = seed_facility(db, large=large, anchor=anchor)
                snapshot = build_snapshot(db, facility, horizon_hours=horizon_hours)
                by_algorithm: dict[str, dict] = {}
                for name, algorithm, objective in (
                    ("fcfs", "fcfs", "dispatch rule"),
                    ("spt", "spt", "dispatch rule"),
                    ("edd", "edd", "dispatch rule"),
                    ("cp_sat_makespan", "cp_sat", "makespan"),
                    ("cp_sat_tardiness", "cp_sat", "total_tardiness"),
                ):
                    result = schedule(
                        snapshot,
                        algorithm=algorithm,
                        objective=objective,
                        time_limit_seconds=cp_sat_limit,
                        random_seed=0,
                    )
                    by_algorithm[name] = result
                    print(
                        f"{size:8} {name:18} {result['status']:10} "
                        f"{result['runtime_ms']:9.1f} ms",
                        flush=True,
                    )
                results[size] = {"snapshot": snapshot, "algorithms": by_algorithm}
    finally:
        engine.dispose()
    return results


def markdown_report(results: dict[str, dict], anchor: date, cp_sat_limit: float, horizon_hours: int) -> str:
    import ortools
    import simpy

    lines = [
        "# Measured scheduling benchmarks",
        "",
        "These results use **fictional Northline sample data** and actual engine outputs. They are not measurements from a physical factory.",
        "",
        f"- Fixed planning anchor: {anchor.isoformat()} (Monday, America/Chicago).",
        f"- Horizon: {horizon_hours} hours from each dataset's earliest release.",
        "- Algorithms: FCFS, SPT, and EDD dispatch; OR-Tools CP-SAT with makespan and total-tardiness objectives.",
        f"- CP-SAT wall time limit: {cp_sat_limit:g} seconds per objective; random seed 0; one solver worker.",
        "- Runtime is a single measured engine call, including normalization, validation, and metric calculation. It excludes database seeding and API transport.",
        f"- Environment: Python {platform.python_version()} on {platform.system()} {platform.machine()}; OR-Tools {ortools.__version__}; SimPy {simpy.__version__}.",
        "",
        "The same persisted inputs, calendar windows, maintenance, machine options, and horizon are used for each algorithm within a dataset. Average utilization is the arithmetic mean of machines with available time from origin through makespan. On-time rate uses completed jobs as the denominator.",
        "",
    ]
    for size, group in results.items():
        snapshot = group["snapshot"]
        lines.extend(
            [
                f"## {size}, {len(snapshot['machines'])} machines",
                "",
                f"Input: {sum(len(job['operations']) for job in snapshot['jobs'])} operations; origin {snapshot['origin']}.",
                "",
                "| Algorithm | Objective | Status | Makespan (min) | Total tardiness (job-min) | On-time rate | Avg. utilization | Engine runtime (ms) |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for name, label, objective in (
            ("fcfs", "FCFS", "dispatch rule"),
            ("spt", "SPT", "dispatch rule"),
            ("edd", "EDD", "dispatch rule"),
            ("cp_sat_makespan", "CP-SAT", "min. makespan"),
            ("cp_sat_tardiness", "CP-SAT", "min. total tardiness"),
        ):
            lines.append("| " + _row(label, objective, group["algorithms"][name]))
        failed = [
            (name, result["diagnostics"])
            for name, result in group["algorithms"].items()
            if result["status"] not in {"feasible", "optimal"}
        ]
        if failed:
            lines.extend(["", "Run diagnostics:"])
            for name, diagnostics in failed:
                lines.append(f"- {name.upper()}: {'; '.join(diagnostics) or 'No feasible result returned.'}")
        lines.append("")
    lines.extend(
        [
            "## Interpretation and limits",
            "",
            "The dispatching rules make local decisions at each earliest feasible start. CP-SAT searches the full flexible job-shop model. An `Optimal` status means the solver proved the selected objective; `Feasible` means it found a valid schedule without proving optimality before the limit. The CP-SAT rows expose the tradeoff between finishing all work early and reducing delivery delay. On-time rate is measured from the resulting schedules and is not directly optimized by either CP-SAT objective.",
            "",
            "These are single-run performance observations on one machine and one fixed synthetic dataset. They do not predict production savings, solver speed on other facilities, or the winner under a different objective. Processing times and due dates are demonstration assumptions. See [Algorithms](algorithms.md) and [Metric definitions](metrics.md) for exact rules.",
            "",
            "A time-limited `FEASIBLE` CP-SAT incumbent can change across reruns: the wall-clock cutoff and competing system load affect how far the search proceeds even with a fixed random seed. Regenerate this table on the target machine for a current measurement.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchor-date", default="2026-11-16", help="Fixed planning Monday, YYYY-MM-DD")
    parser.add_argument("--cp-sat-limit-seconds", type=float, default=10.0)
    parser.add_argument("--horizon-hours", type=int, default=336)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "benchmarks.md")
    args = parser.parse_args()
    anchor = date.fromisoformat(args.anchor_date)
    if anchor.weekday() != 0:
        parser.error("--anchor-date must be a Monday")
    if args.cp_sat_limit_seconds <= 0:
        parser.error("--cp-sat-limit-seconds must be positive")
    if args.horizon_hours <= 0:
        parser.error("--horizon-hours must be positive")
    results = run_benchmark(anchor, args.cp_sat_limit_seconds, args.horizon_hours)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown_report(results, anchor, args.cp_sat_limit_seconds, args.horizon_hours), encoding="utf-8")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
