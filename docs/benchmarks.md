# Measured scheduling benchmarks

These results use **fictional Northline sample data** and actual engine outputs. They are not measurements from a physical factory.

- Fixed planning anchor: 2026-11-16 (Monday, America/Chicago).
- Horizon: 336 hours from each dataset's earliest release.
- Algorithms: FCFS, SPT, and EDD dispatch; OR-Tools CP-SAT with makespan and total-tardiness objectives.
- CP-SAT wall time limit: 10 seconds per objective; random seed 0; one solver worker.
- Runtime is a single measured engine call, including normalization, validation, and metric calculation. It excludes database seeding and API transport.
- Environment: Python 3.12.14 on Darwin arm64; OR-Tools 9.15.6755; SimPy 4.1.2.

The same persisted inputs, calendar windows, maintenance, machine options, and horizon are used for each algorithm within a dataset. Average utilization is the arithmetic mean of machines with available time from origin through makespan. On-time rate uses completed jobs as the denominator.

## 20 jobs, 6 machines

Input: 60 operations; origin 2026-11-16T12:00:00Z.

| Algorithm | Objective | Status | Makespan (min) | Total tardiness (job-min) | On-time rate | Avg. utilization | Engine runtime (ms) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| FCFS | dispatch rule | Feasible | 1,974 | 483 | 85.0% | 50.3% | 25.9 |
| SPT | dispatch rule | Feasible | 2,036 | 439 | 85.0% | 48.7% | 18.9 |
| EDD | dispatch rule | Feasible | 1,981 | 252 | 85.0% | 50.4% | 19.6 |
| CP-SAT | min. makespan | Optimal | 1,965 | 477 | 85.0% | 50.6% | 1,336.1 |
| CP-SAT | min. total tardiness | Optimal | 2,242 | 160 | 85.0% | 42.6% | 128.8 |

## 100 jobs, 10 machines

Input: 300 operations; origin 2026-11-16T12:00:00Z.

| Algorithm | Objective | Status | Makespan (min) | Total tardiness (job-min) | On-time rate | Avg. utilization | Engine runtime (ms) |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| FCFS | dispatch rule | Feasible | 4,857 | 7,951 | 74.0% | 69.9% | 1,024.6 |
| SPT | dispatch rule | Feasible | 4,854 | 8,272 | 71.0% | 70.0% | 1,183.4 |
| EDD | dispatch rule | Feasible | 4,854 | 1,316 | 88.0% | 70.2% | 741.8 |
| CP-SAT | min. makespan | Optimal | 4,815 | 8,309 | 71.0% | 70.8% | 5,293.7 |
| CP-SAT | min. total tardiness | Feasible (not proven optimal) | 4,997 | 1,247 | 89.0% | 67.3% | 12,625.6 |

## Interpretation and limits

The dispatching rules make local decisions at each earliest feasible start. CP-SAT searches the full flexible job-shop model. An `Optimal` status means the solver proved the selected objective; `Feasible` means it found a valid schedule without proving optimality before the limit. The CP-SAT rows expose the tradeoff between finishing all work early and reducing delivery delay. On-time rate is measured from the resulting schedules and is not directly optimized by either CP-SAT objective.

These are single-run performance observations on one machine and one fixed synthetic dataset. They do not predict production savings, solver speed on other facilities, or the winner under a different objective. Processing times and due dates are demonstration assumptions. See [Algorithms](algorithms.md) and [Metric definitions](metrics.md) for exact rules.

The 100-job tardiness row is a time-limited feasible incumbent, so its exact value is not stable across runs. An independent rerun of the same inputs and ten-second limit on this host found 994 job-minutes, 92.0% on-time delivery, and a 4,998-minute makespan; optimality was still not proven. The fixed seed makes model construction and search choices repeatable, but wall-clock cutoff and system load can change how far the search gets. Regenerate this table with `PYTHONPATH=services/api .venv/bin/python scripts/benchmark.py` for a current measurement.
