# Local startup and deployment boundary

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

## GitHub and Vercel publishing

After pushing the repository to GitHub, import it into Vercel as a Next.js project with **Root Directory** set to `apps/web`. Set `API_INTERNAL_URL` in Vercel to the HTTPS origin of a separately hosted API, without `/api/v1` at the end. The web app requests relative `/api/v1/*` paths, which Next.js rewrites to that origin. The default `http://localhost:8000` destination works only locally.

Host `services/api` as one persistent Python 3.12 service that permits child processes. Schedules and simulations continue in local worker processes after the request returns, so the API cannot provide its full functionality on Vercel serverless as written. Use persistent PostgreSQL, set a private `DATABASE_URL`, and run `alembic upgrade head` before starting the API. A multi-instance API needs a durable worker queue and shared ownership first. The supplied API Docker command and the default `AUTO_SEED=1` both seed fictional demo data; adjust startup if that data should not appear in the deployed database.

The web rewrite keeps browser requests on the web origin. Set `CORS_ORIGINS` to the web origin if browsers will call the API directly. The current API has no accounts or authorization: protect its writable routes before exposing them publicly, or disable writes for a read-only demo. Use deployment-specific database credentials rather than the local Compose credentials.

## Environment

Copy `.env.example` to `.env` when configuring manual runs. The relevant variables are:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy connection URL; PostgreSQL in Compose, SQLite for local fallback |
| `API_INTERNAL_URL` | Server-side Next.js rewrite destination for `/api/v1/*` |
| `NEXT_PUBLIC_API_BASE_URL` | Browser API prefix; normally `/api/v1` |
| `CORS_ORIGINS` | Allowed browser origins for direct API access |
| `AUTO_SEED` | Seed the fictional demo dataset on API startup (`1` by default) |
| `LINEPILOT_SOLVER_TIME_LIMIT_SECONDS` | Default CP-SAT time limit |
| `DEMO_ANCHOR_DATE` | Optional Monday (`YYYY-MM-DD`) to fix fictional demo dates for reproducible experiments |

The demonstration installation has a single local workspace and no account system. It is intended for local development and evaluation.
