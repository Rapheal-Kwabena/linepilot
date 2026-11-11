# Local startup and deployment

## Docker Compose

With Docker Compose installed, run from the repository root:

```sh
docker compose up --build
```

The web app is at `http://localhost:3000`, the API at `http://localhost:8000`, and generated OpenAPI documentation at `http://localhost:8000/docs`. Compose starts PostgreSQL 16, applies Alembic migrations, seeds the fictional Northline Components Plant dataset, then starts the API and web app. PostgreSQL data persists in the `linepilot_pgdata` Docker volume. Services bind to `127.0.0.1` for local use.

## Local processes without Docker

Use Python 3.12 or newer and Node.js 22 or newer:

```sh
make setup
make api
```

Start the frontend in another terminal:

```sh
make web
```

The API uses a local SQLite database if `DATABASE_URL` is unset. Pass a specific Python executable through `PYTHON_BOOTSTRAP` if `python3.12` is not on `PATH`. `make api` migrates and seeds the database before starting Uvicorn.

## GitHub and Vercel Services

The repository-root `vercel.json` defines three services: public Next.js `web`, private FastAPI `api`, and a private scheduled `scheduler`. When connecting the GitHub repository to the Vercel project, use its **repository root** so Vercel reads that file; do not set the project Root Directory to `apps/web`. The web service receives an `API_INTERNAL_URL` service binding and proxies `/api/v1/*` at request time. The API has no public rewrite. The web proxy denies `/api/v1/internal/*`.

Queue mode uses the Python subscriber registered in `services/api/pyproject.toml`. Schedule and simulation requests persist a `queued` row and return HTTP 202; a Vercel Queue message carries only its run ID. The subscriber reads the snapshot from PostgreSQL, computes the result, and saves it under a database claim. Duplicate deliveries and late results cannot overwrite a completed or cancelled run. The private scheduled function checks for abandoned rows on a 15-minute schedule, subject to Vercel's dispatch jitter, and republishes them. It calls `POST /api/v1/internal/reconcile-runs` through its API service binding with `Authorization: Bearer <CRON_SECRET>`. That endpoint fails closed without the secret. Before deploying, run `vercel build` on Linux to confirm that the output contains a FastAPI handler, a Queue subscriber with a queue trigger, and the scheduler function with a `schedule/v1beta` trigger. Vercel scheduled functions in a service are not run by `vercel dev`; use **Schedules → Run Schedule** after deployment.

Deploy in this order:

1. Provision managed PostgreSQL, such as Neon through Vercel Marketplace, or use the project's existing integration. Confirm that a server-side `DATABASE_URL` is configured for **Production**. The API and queue subscriber must use the same database. The application accepts the provider's `postgresql://` URL and selects the installed psycopg driver internally; keep its credentials and TLS query options intact. Do not use the local Compose credentials. Give Preview deployments a separate database before running write operations there.
2. From a trusted environment with the API dependencies installed, export `DATABASE_URL` with the exact Production database URL, then apply migrations. Do not rely on copying the repository-root `.env.example`: Python and Alembic read exported environment variables and do not load that file. Run the seed command once if the fictional Northline demo should appear:

   ```sh
   cd services/api
   ../../.venv/bin/alembic upgrade head
   ../../.venv/bin/python -m app.seed
   ```

   Seeding uses the next Monday in the facility timezone by default. Set `DEMO_ANCHOR_DATE=2026-11-16` for this command only when the published benchmark dates are needed. Seeding is idempotent, and a later run does not reset existing data. Queue mode defaults `AUTO_SEED` to `0`; set it explicitly to `0` for the deployment so cold starts do not seed.
3. In Vercel project environment settings, set `LINEPILOT_RUN_BACKEND=vercel_queue`, `AUTO_SEED=0`, and a strong `CRON_SECRET` in Production. The API and queue subscriber read the same `DATABASE_URL`; the API and scheduler read the same `CRON_SECRET`. Set the corresponding values with a separate database and secret for Preview before testing a preview deployment. On Vercel, the API fails startup if queue mode or a persistent PostgreSQL URL is missing. The `API_INTERNAL_URL` values for web and scheduler come from service bindings in `vercel.json`; do not set that variable manually in Vercel. The browser API prefix is fixed at `/api/v1`.
4. Enable **Vercel Authentication → All Deployments** in the project before connecting Git and triggering its first deployment. This is a single-workspace demo without application accounts or per-user permissions; anyone admitted to the deployment can change its data.
5. Connect the GitHub repository at its root and deploy. In an authenticated browser or with `vercel curl`, check `/api/v1/health` for database connectivity and `/api/v1/bootstrap` for the migrated, seeded schema. Generate an FCFS schedule, poll its run ID until completion, run a simulation, and check cancellation and reports. Trigger the reconciliation schedule once in Vercel's Schedules UI and inspect its logs. The service binding, queue delivery, and schedule are configured in the repository but require a live-account deployment to verify.

Queue subscribers are subject to the selected Vercel plan's function duration and memory limits. The API bounds CP-SAT time limits and simulation replication counts, but large models still need a live deployment check. Local Compose and `make api` continue to use `LINEPILOT_RUN_BACKEND=local` with isolated processes. SQLite remains a local fallback.

## Environment

Use `.env.example` as a list of local defaults. Export values in the shell that starts Python, Alembic, or `make`; copying the file to `.env` alone does not load it. The relevant variables are:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy connection URL; PostgreSQL in Compose, SQLite for local fallback |
| `API_INTERNAL_URL` | Generated service binding for the web proxy and private scheduler; local web defaults to `127.0.0.1:8000`. Do not set it manually in Vercel. |
| `CORS_ORIGINS` | Allowed origins if a browser calls the API directly; the deployed API is private |
| `AUTO_SEED` | Seed fictional data on startup (`1` in local mode, `0` in queue mode by default) |
| `LINEPILOT_RUN_BACKEND` | `local` for processes or `vercel_queue` for the Vercel subscriber |
| `CRON_SECRET` | Shared bearer secret for the private reconciliation request |
| `LINEPILOT_SOLVER_TIME_LIMIT_SECONDS` | Default CP-SAT time limit |
| `DEMO_ANCHOR_DATE` | Optional Monday (`YYYY-MM-DD`) to fix fictional demo dates for reproducible experiments |

The demonstration installation has a single local workspace and no account system. It is intended for local development and evaluation.
