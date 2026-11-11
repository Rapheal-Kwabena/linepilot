"""Provider URLs must select psycopg without changing connection details."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app import seed as seed_module
from app.core.database_url import normalize_database_url, resolve_database_url


@pytest.mark.parametrize("provider_scheme", ["postgresql", "postgres", "PostgreSQL"])
def test_provider_postgres_url_keeps_credentials_and_tls_query(provider_scheme):
    suffix = "user:p%40ss%2Fword@db.example:5432/linepilot?sslmode=require&options=endpoint%3Dep-1"
    normalized = normalize_database_url(f"{provider_scheme}://{suffix}")
    assert normalized == f"postgresql+psycopg://{suffix}"

    engine = create_engine(normalized)
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://user:secret@db.example/linepilot?sslmode=require",
        "sqlite:///./linepilot.db",
        "mysql+pymysql://user:secret@db.example/linepilot",
    ],
)
def test_explicit_driver_and_other_database_urls_are_unchanged(url):
    assert normalize_database_url(url) == url


@pytest.mark.parametrize(
    "configured_url",
    [None, "", "sqlite:///./linepilot.db", "sqlite+pysqlite:///:memory:", "mysql://user:secret@db/linepilot"],
)
def test_vercel_requires_shared_postgres_without_exposing_url(configured_url):
    with pytest.raises(RuntimeError, match="DATABASE_URL must be a persistent PostgreSQL URL") as error:
        resolve_database_url(configured_url, on_vercel=True)
    assert "secret" not in str(error.value)


def test_local_default_and_vercel_provider_url():
    assert resolve_database_url(None, on_vercel=False) == "sqlite:///./linepilot.db"
    provider_url = "postgresql://user:p%40ss@db.example/linepilot?sslmode=require"
    assert resolve_database_url(provider_url, on_vercel=True) == normalize_database_url(provider_url)


def test_vercel_runtime_fails_at_import_when_database_url_is_missing():
    env = os.environ.copy()
    env["VERCEL"] = "1"
    env.pop("DATABASE_URL", None)
    result = subprocess.run(
        [sys.executable, "-c", "import app.core.db"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "DATABASE_URL must be a persistent PostgreSQL URL on Vercel" in result.stderr


def test_postgres_seed_requires_migration_before_creating_tables(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(seed_module, "DATABASE_URL", "postgresql+psycopg://db.example/linepilot")
    monkeypatch.setattr(seed_module, "engine", engine)
    try:
        with pytest.raises(RuntimeError, match="Apply Alembic migrations before seeding"):
            seed_module._ensure_seed_schema()
        assert not inspect(engine).has_table("workspaces")
    finally:
        engine.dispose()


def test_sqlite_seed_still_bootstraps_schema(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(seed_module, "DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(seed_module, "engine", engine)
    try:
        seed_module._ensure_seed_schema()
        assert inspect(engine).has_table("workspaces")
    finally:
        engine.dispose()
