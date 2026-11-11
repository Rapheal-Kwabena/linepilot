# LINEPILOT

**Manufacturing Intelligence & Operations Optimization**

Better decisions. Measurable operations.

LINEPILOT is a desktop-first engineering workstation for modeling a production facility, generating constrained schedules, replaying production events, testing disruptions, and exporting the evidence behind a planning decision. The included **Northline Components Plant** is fictional sample data; every displayed schedule and metric is calculated from stored inputs.

## Start locally

With Docker Compose:

```sh
docker compose up --build
```

Open `http://localhost:3000`. The API and OpenAPI documentation are at `http://localhost:8000` and `http://localhost:8000/docs`. Compose runs PostgreSQL, applies migrations, and seeds the Northline demo.

Without Docker, use Python 3.12+ and Node.js 22+:

```sh
make setup
make api
```

In a second terminal:

```sh
make web
```

This path uses a local SQLite database by default. If Python 3.12 is not on `PATH`, run `make setup PYTHON_BOOTSTRAP=/absolute/path/to/python3.12`. The seed is idempotent. Set `DEMO_ANCHOR_DATE=2026-11-16` before seeding to reproduce the published experiment dates.

## Demo journey

1. Open **Production Planner** to inspect the 20 seeded jobs, 60 ordered operations, six machines, three work centers, shifts, and planned maintenance.
2. Open **Scheduling Studio** and generate FCFS, SPT, EDD, or CP-SAT schedules. Each run persists its exact input snapshot, assignments, solver status, diagnostics, and metrics.
3. Inspect the machine Gantt chart, then compare two runs. The interface identifies when their scheduling inputs differ.
4. Create a scenario, recompute the schedule, and compare the measured effect with the baseline without changing its source records.
5. Run deterministic SimPy playback or a seeded stochastic simulation. The factory view and event log follow stored simulation events.
6. Inspect bottleneck evidence and resource utilization, then generate a CSV or PDF engineering report from a saved run.

The demo API journey can be checked against a running server with:

```sh
.venv/bin/python scripts/smoke_api.py --base http://localhost:8000
```

To add the separate 10-machine, 100-job fictional load-test facility to a local database, run `cd services/api && ../../.venv/bin/python -m app.seed --large`, then choose it from the facility selector. This is a larger engineering test input, not an observed factory dataset.

## Engineering model

- FCFS, SPT, and EDD are resource-constrained dispatch rules. CP-SAT uses optional machine intervals, precedence, release times, calendars, and downtime. A separate validator checks every produced assignment before it is persisted as valid.
- SimPy produces deterministic schedule playback and configurable stochastic replications. Random assumptions are labeled as model inputs, not measured factory reliability.
- Metrics are calculated centrally from actual assignments or simulation events. Makespan, tardiness, throughput, utilization, waiting, and machine operating cost have documented definitions.
- Schedule and simulation requests return a run ID for polling. Local mode uses isolated processes; the Vercel deployment uses Queue subscribers with database-backed claims and persisted cancellation.

See [Architecture](docs/architecture.md), [Algorithms](docs/algorithms.md), [Metrics](docs/metrics.md), [Data model](docs/data-model.md), [Design system](docs/design-system.md), and [Deployment notes](docs/deployment.md). The [measured benchmarks](docs/benchmarks.md) and [portfolio case study](docs/case-study.md) use fixed fictional inputs and report actual solver status.

## Verify and benchmark

```sh
make test-backend
make test-frontend
PYTHONPATH=services/api .venv/bin/python scripts/benchmark.py
PYTHONPATH=services/api .venv/bin/python scripts/case_study_experiment.py
```

With the API and web app running against the seeded local workspace, run `make test-e2e` for browser tests that exercise the real backend. Playwright needs a compatible Chromium installation; set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` if your browser is already installed at a custom path.

The benchmark script seeds an isolated in-memory database and records fresh measurements. Time-limited CP-SAT feasible incumbents may vary with system load; only a run marked `OPTIMAL` has proven its selected objective.

## Deployment

The included Compose setup binds to localhost and uses local worker processes. The repository-root [Vercel configuration](vercel.json) defines a public Next.js service, a private FastAPI service, and a private scheduled reconciliation function. Vercel Queue subscribers run schedules and simulations; both API and subscribers need the same managed PostgreSQL database. Apply Alembic migrations and seed the fictional demo once before using the deployment. Enable Vercel Authentication for **All Deployments** because the demo has no application accounts or per-user permissions. The Vercel service binding, queue delivery, and scheduled function still require validation in a live Vercel project. See the [deployment notes](docs/deployment.md) for exact setup and checks. No physical machine, sensor, or IoT connection is required.
