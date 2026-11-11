"""Consistent API errors and record lookup helpers."""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, details=None):
        super().__init__(
            status_code=status_code, detail={"code": code, "message": message, "details": details}
        )


def get_or_404(db: Session, model, entity_id: str, label: str):
    try:
        UUID(entity_id)
    except (TypeError, ValueError):
        raise ApiError(404, "not_found", f"{label} was not found") from None
    entity = db.get(model, entity_id)
    if entity is None:
        raise ApiError(404, "not_found", f"{label} was not found")
    return entity


def invalid(message: str, details=None) -> ApiError:
    return ApiError(422, "invalid_input", message, details)
