"""Adapt provider PostgreSQL URLs to the installed SQLAlchemy driver."""

from __future__ import annotations

LOCAL_DATABASE_URL = "sqlite:///./linepilot.db"


def normalize_database_url(url: str) -> str:
    """Replace only the scheme, preserving encoded credentials and TLS options."""
    scheme, separator, remainder = url.partition("://")
    if separator and scheme.lower() in {"postgres", "postgresql"}:
        return f"postgresql+psycopg://{remainder}"
    return url


def resolve_database_url(configured_url: str | None, *, on_vercel: bool) -> str:
    """Require shared PostgreSQL on Vercel; retain SQLite for local startup."""
    if not configured_url:
        if on_vercel:
            raise RuntimeError("DATABASE_URL must be a persistent PostgreSQL URL on Vercel")
        configured_url = LOCAL_DATABASE_URL
    url = normalize_database_url(configured_url)
    scheme, _, _ = url.partition("://")
    if on_vercel and scheme.lower() != "postgresql+psycopg":
        raise RuntimeError("DATABASE_URL must be a persistent PostgreSQL URL on Vercel")
    return url
