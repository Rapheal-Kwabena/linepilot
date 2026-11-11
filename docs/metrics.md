# Engineering metric definitions

All times in the engine are integer minutes from a UTC schedule origin. Local facility time is a presentation concern. Schedule and simulation results have separate labels because scheduled values are predictions while simulated values are outputs of a model.

| Metric | Definition | Unit |
| --- | --- | --- |
| Makespan | Latest completed job end minus schedule origin | minutes |
| Job flow time | Job completion minus release | minutes |
| Job waiting time | Flow time minus the sum of the job's assigned operation processing durations | minutes |
| Tardiness | `max(0, completion - due)` for each job | minutes |
| Total tardiness | Sum of job tardiness | job-minutes |
| Weighted tardiness | Sum of job tardiness multiplied by its configured priority weight | weighted job-minutes |
| On-time delivery | Jobs completed by due time divided by completed jobs with a due time | fraction / percent |
| Throughput | Completed jobs divided by the stated observation duration | jobs per hour |
| Machine utilization | Busy processing minutes divided by available scheduled operating minutes in the stated interval | fraction / percent |
| Average waiting | Arithmetic mean of completed jobs' waiting times | minutes |
| Operating cost | Sum over assigned processing time × machine hourly operating cost / 60 | configured currency units |
| Work in progress | Jobs released and not yet completed at a simulation instant | jobs |

Available time is the union of machine operating-calendar intervals within the observation period, less the union of scheduled downtime intervals. Closed hours and downtime are not counted as idle available time. A zero available-time denominator produces `N/A` with a reason rather than a utilization percentage.

Resource Utilization accepts a facility-local date range and converts it to a half-open interval of UTC minutes from the saved schedule origin. Busy assignments, calendar-open time, and downtime are clipped to that same interval. `Unavailable` is calendar-closed time plus planned downtime; `idle available` is available time minus busy time. The heatmap and machine trend use the same calculation in one-hour, four-hour, or larger buckets chosen to keep long schedules readable. The available-versus-assigned chart compares saved processing assignments with machine capacity, not unscheduled demand. A valid schedule has zero overtime because the validator forbids work outside availability.

The due-workload capacity screen counts each saved machine assignment still unfinished at the selected interval start when its job is due by that interval's end. Work scheduled after the end is included because it would miss that deadline. `Capacity gap = max(0, due workload − available time)` for each machine. This is a screening measure based on the saved assignment; it does not prove deadline feasibility because operation precedence, job release timing, and alternative machine choices also matter.

For schedule comparisons, both runs must use the same input dataset and metric definitions. A percentage change is shown only when the baseline denominator is nonzero. A smaller makespan or lower tardiness does not by itself imply a financial saving. The cost calculation includes only configured machine operating cost and excludes unmodeled labor and inventory costs.

For simulation, flow and waiting are derived from actual start/completion events. Replication means summarize multiple runs and should state the random seed, variability distribution, and observation horizon. The assumed variability is a model input, not a measured factory statistic.
