# Scheduling and simulation methods

LINEPILOT models a flexible job shop. A job contains ordered operations, and each operation lists one or more eligible machines with a processing duration for each machine. The input is a versioned snapshot stored with every schedule run. All engine times are integer minutes from a UTC origin. Machine calendars become available intervals; maintenance and scenario downtime are subtracted before scheduling. Operations are nonpreemptive.

## Dispatching baselines

FCFS, SPT, and EDD are feasible scheduling rules, not sorted job lists. At each decision, the engine considers each job's next operation, finds its earliest continuous free slot on every eligible machine, and chooses the option with earliest start, then earliest finish. Among operations that can start at the same decision time, the rule ranks by:

| Rule | Tie priority |
| --- | --- |
| FCFS | Earlier job release, then job number |
| SPT | Shorter selected processing duration, then release and job number |
| EDD | Earlier job due time, then release and job number |

After a choice, the machine interval is occupied and the next operation of that job becomes eligible at the previous operation's end. A dispatch rule can fail to place every operation in a bounded horizon even when a different assignment would fit; this is reported as an infeasible *dispatch result*, not a proof that the complete mathematical model has no solution.

## CP-SAT model

OR-Tools CP-SAT uses integer minute start and end variables for each operation. Each eligible machine option has a Boolean assignment variable and an optional interval whose duration is the machine-specific processing time. `ExactlyOne` chooses one option per operation. `NoOverlap` applies to each machine's selected operation intervals and fixed blocked intervals for closed calendar time and downtime. Precedence inequalities order each job's operations. Release constraints forbid starts before job arrival, and all intervals lie within the configured horizon.

For job \(j\), completion \(C_j\) is its final operation end and tardiness is \(T_j=\max(0,C_j-d_j)\), where \(d_j\) is its due minute. The engine supports makespan \(\max_j C_j\), total tardiness \(\sum_j T_j\), and priority-weighted tardiness \(\sum_j p_j T_j\). Its optional balanced objective minimizes \(n\cdot\text{makespan}+\sum_j p_jT_j\); the multiplication keeps integer coefficients and gives one minute of makespan the same weight as one minute of average weighted tardiness. This weighting is a modeling decision, not a universal business optimum.

CP-SAT has a configurable search time limit, random seed, and one to eight search workers. One worker is the deterministic default. Using several workers can change the search path and the feasible incumbent found before a wall-clock cutoff. The saved run records its solver configuration. `OPTIMAL` means optimality was proven for the selected model and objective. `FEASIBLE` means a valid schedule was found without proof of optimality. `INFEASIBLE` means the bounded model has no feasible solution. `UNKNOWN` means no feasible result was found before the solver stopped. A terminated run is recorded as cancelled.

Before CP-SAT search, the engine computes independently validated FCFS, SPT, and EDD schedules as candidate warm starts. It passes the strongest dispatch schedule for the selected objective as a solver hint and adds its objective value as an upper bound. The configured time limit applies to CP-SAT search; reported engine runtime also includes warm-start generation and validation.

## Independent validation

The solver and dispatch rules do not certify themselves. Before the API persists a generated schedule, a separate validator checks every operation appears exactly once, chosen machines are eligible, machine-specific durations match, job release and precedence hold, machine assignments do not overlap, and every operation fits within an available machine window and the bounded horizon. Invalid output is treated as an error with diagnostics.

## Discrete-event simulation

SimPy runs the facility event stream. Deterministic playback reproduces a stored valid schedule with job release, queue entry, processing start/end, machine downtime, and job completion events. The interface's play, pause, step, and speed controls consume this event stream; playback speed has no effect on calculated results.

Stochastic mode keeps the schedule's machine assignments while sampling processing durations from a fixed, triangular, or lognormal distribution. Optional failures use exponentially distributed time between failures and repair durations. A seeded run is reproducible. Machines have capacity one and operations queue for resources; a task starts only when a continuous machine window can fit its sampled processing time. Jobs that cannot complete within the horizon are marked unfinished. Multiple replications produce sample means and, when there is enough data, approximate 95% intervals. These are modeled scenarios, not measured plant reliability estimates.

## Practical limits

Flexible job-shop scheduling is computationally difficult. CP-SAT run time can grow sharply with jobs, machine alternatives, calendar fragmentation, and long horizons. The API caps input size and solver time, reports the actual solver status, and retains the best feasible solution when optimality is not proven. The model does not currently include explicit labor crews, material shortages, sequence-dependent setup, transport time, or energy tariffs; these would require additional constraints and data rather than inferred savings.
