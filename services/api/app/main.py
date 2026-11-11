"""LINEPILOT versioned FastAPI application."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import imports, reports, resources, runs
from app.core.db import DATABASE_URL, Base, SessionLocal, engine
from app.models import *  # noqa: F403 - register all metadata tables before create_all
from app.seed import seed_facility
from app.workers.runs import recover_interrupted_runs


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            },
            ensure_ascii=False,
        )


handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.getLogger("app").addHandler(handler)
logging.getLogger("app").setLevel(logging.INFO)
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    # SQLite is the zero-configuration local path. PostgreSQL deployments run
    # Alembic migrations before startup (see docker-compose.yml).
    if DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(engine)
    if os.getenv("AUTO_SEED", "1") == "1":
        with SessionLocal() as db:
            seed_facility(db)
    recover_interrupted_runs()
    yield


app = FastAPI(
    title="LINEPILOT API",
    description="Manufacturing data, constrained schedules, simulations and engineering analysis.",
    version="1.0.0",
    lifespan=lifespan,
)

origins = [
    item.strip() for item in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:  # noqa: ARG001
    if isinstance(exc.detail, dict) and {"code", "message"}.issubset(exc.detail):
        detail = exc.detail
    else:
        detail = {"code": "http_error", "message": str(exc.detail), "details": None}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:  # noqa: ARG001
    details = [
        {"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
        for item in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "error": {"code": "validation_error", "message": "Request validation failed", "details": details}
        },
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
    logger.exception("Unhandled API error")
    return JSONResponse(
        status_code=500,
        content={
            "error": {"code": "internal_error", "message": "An internal error occurred", "details": None}
        },
    )


@app.get("/health")
@app.get("/api/v1/health")
def health() -> dict:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "database_unavailable", "message": "Database check failed", "details": None},
        ) from exc
    return {"status": "ok", "database": "connected"}


app.include_router(resources.router, prefix="/api/v1", tags=["Resources"])
app.include_router(runs.router, prefix="/api/v1", tags=["Engineering runs"])
app.include_router(imports.router, prefix="/api/v1", tags=["Imports"])
app.include_router(reports.router, prefix="/api/v1", tags=["Reports"])
