# Case study: planning with competing production objectives

## Operating question

How should a planner schedule a fictional components plant when machine capacity, shift calendars, maintenance, operation order, and delivery deadlines all matter? A shorter overall schedule may still finish more orders late. LINEPILOT was built to expose that choice with validated schedules and measured results.

The Northline Components Plant sample has three work centers, six machines, and 20 jobs with 60 ordered operations. A second load test facility has ten machines and 100 jobs with 300 operations. Every operation has at least one eligible machine with a machine-specific processing time. Both datasets have planned maintenance and mixed due dates. These are generated demonstration inputs, not actual factory records.

## Method

Persisted jobs, machine options, calendars, and downtime are converted into an immutable UTC-relative scheduling snapshot. FCFS, shortest processing time (SPT), and earliest due date (EDD) dispatching each produce a resource-constrained baseline. OR-Tools CP-SAT then solves a flexible job-shop model with optional machine intervals, `NoOverlap`, job precedence, release times, and blocked calendar intervals. A separate validator checks every returned assignment before it can be used. SimPy replays validated schedules from event records; stochastic replications are available for experiments that explicitly state their assumptions.

The benchmark fixes the planning anchor at **November 16, 2026**, uses a **336-hour horizon**, and caps each CP-SAT objective at **10 seconds of solver time**. All five rows within a dataset use the same underlying jobs, machines, and calendar. Runtime was measured on Python 3.12 on an arm64 Mac and includes engine normalization, validation, and metric calculation. See the full [benchmark record](benchmarks.md) and [metric definitions](metrics.md).

## Measured schedule results

| Dataset | Strategy and objective | Makespan | Total tardiness | On-time jobs | Status |
| --- | --- | ---: | ---: | ---: | --- |
| 20 jobs | FCFS | 1,974 min | 483 job-min | 85% | Feasible |
| 20 jobs | EDD | 1,981 min | 252 job-min | 85% | Feasible |
| 20 jobs | CP-SAT, minimize makespan | 1,965 min | 477 job-min | 85% | Optimal |
| 20 jobs | CP-SAT, minimize tardiness | 2,242 min | 160 job-min | 85% | Optimal |
| 100 jobs | EDD | 4,854 min | 1,316 job-min | 88% | Feasible |
| 100 jobs | CP-SAT, minimize makespan | 4,815 min | 8,309 job-min | 71% | Optimal |
| 100 jobs | CP-SAT, minimize tardiness | 4,997 min | 1,247 job-min | 89% | Feasible; optimality not proven |

For 20 jobs, the makespan objective cut nine minutes from FCFS but did little for delivery delay. The tardiness objective reduced total delay to 160 job-minutes, while extending makespan by 277 minutes compared with the makespan optimum. In the 100-job test, EDD produced far less tardiness than the proven makespan optimum. CP-SAT's tardiness objective found a valid 1,247 job-minute schedule, but the ten-second limit ended before optimality was proven. This shows why the planner must select an objective that matches the operational decision. A proven optimum for one objective is not automatically the best result for another.

## Controlled downtime experiment

Using the 20-job dataset and FCFS in both cases, a hypothetical failure was added to **M-01 from 14:00 to 18:00 UTC on November 16, 2026** (08:00–12:00 local plant time). This scenario was applied to a copy of the baseline snapshot; it did not edit the underlying facility. The schedules were validated and then replayed through SimPy.

| Measure | Baseline | Four-hour M-01 downtime |
| --- | ---: | ---: |
| Makespan | 1,974 min | 1,974 min |
| Total tardiness | 483 job-min | 529 job-min |
| Late jobs | 3 | 3 |
| Mean simulated job waiting | 64.35 min | 70.35 min |
| Highest scheduled utilization | M-06, 57.0% | M-04, 57.5% |

The extra downtime increased aggregate delivery delay by 46 job-minutes and mean waiting by six minutes even though the final completion time stayed the same. The highest-utilization machine changed, indicating a shift in where work was absorbed. That ranking is evidence for further capacity experiments, not proof that either machine is the causal plant bottleneck.

The experiment can be reproduced against a fresh in-memory copy of the fixed sample data with `PYTHONPATH=services/api .venv/bin/python scripts/case_study_experiment.py` from the repository root. The script validates the schedules and checks the published values.

## Limits and next experiments

The datasets, deadlines, and failure interval are invented inputs. Machine operating cost is modeled, but labor crews, material shortages, sequence-dependent setup, transport, and inventory cost are not. The deterministic replay verifies what the stored schedule implies; it does not forecast real reliability. Stochastic processing and failure assumptions should be calibrated to observed plant data before making operational commitments. The 100-job tardiness run is a feasible incumbent under a time limit, and another run or model configuration could find a better one.

An independent rerun with the same fixed inputs and ten-second limit found a better feasible tardiness value of 994 job-minutes (92% on-time). Neither run proved optimality. This variance is a consequence of stopping a large search by wall time; it should be reported as a timed outcome rather than as a guaranteed result.

A next experiment would compare several capacity or shift changes against the same baseline, run multiple stochastic replications for each, and report both mean delivery performance and uncertainty intervals. Any financial claim would require actual cost and labor data.
