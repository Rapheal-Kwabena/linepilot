"""Validated write contracts for the versioned HTTP API."""

from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import PurePath
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a UTC offset or timezone")
    return value


class WorkspaceCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    timezone: str = "UTC"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unknown IANA timezone") from exc
        return value


class FacilityCreate(StrictModel):
    workspace_id: str
    facility_code: str = Field(min_length=1, max_length=40)
    facility_name: str = Field(min_length=1, max_length=160)
    location_label: str = Field(default="", max_length=160)
    timezone: str = "UTC"
    operating_calendar_id: str | None = None

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return WorkspaceCreate.valid_timezone(value)


class WorkCenterCreate(StrictModel):
    facility_id: str
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    display_order: int = 0
    layout_x: int = 0
    layout_y: int = 0


class CalendarShift(StrictModel):
    day: int = Field(ge=0, le=6)
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def valid_clock(cls, value: str) -> str:
        from datetime import time

        try:
            time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Use HH:MM for a shift time") from exc
        return value


class CalendarCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    timezone: str = "UTC"
    weekly_shifts: list[CalendarShift] = Field(default_factory=list)
    holiday_exceptions: list[str] = Field(default_factory=list)
    special_closures: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        return WorkspaceCreate.valid_timezone(value)

    @field_validator("holiday_exceptions")
    @classmethod
    def valid_holidays(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Holiday exceptions must be YYYY-MM-DD dates") from exc
        return values

    @field_validator("special_closures")
    @classmethod
    def valid_closures(cls, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for item in values:
            try:
                start = datetime.fromisoformat(str(item["start_time"]).replace("Z", "+00:00"))
                end = datetime.fromisoformat(str(item["end_time"]).replace("Z", "+00:00"))
                _aware(start)
                _aware(end)
            except (KeyError, ValueError) as exc:
                raise ValueError("Calendar closures require offset-aware start_time and end_time") from exc
            if end <= start:
                raise ValueError("Calendar closure end_time must follow start_time")
        return values


class CalendarUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = None
    weekly_shifts: list[CalendarShift] | None = None
    holiday_exceptions: list[str] | None = None
    special_closures: list[dict[str, Any]] | None = None


class MachineCreate(StrictModel):
    work_center_id: str
    machine_code: str = Field(min_length=1, max_length=40)
    machine_name: str = Field(min_length=1, max_length=120)
    machine_type: str = Field(default="general", max_length=80)
    status: Literal["available", "maintenance", "out_of_service"] = "available"
    hourly_operating_cost: float = Field(default=0, ge=0)
    operating_calendar_id: str | None = None
    active: bool = True


class MachineUpdate(StrictModel):
    machine_name: str | None = Field(default=None, min_length=1, max_length=120)
    machine_type: str | None = Field(default=None, max_length=80)
    status: Literal["available", "maintenance", "out_of_service"] | None = None
    hourly_operating_cost: float | None = Field(default=None, ge=0)
    operating_calendar_id: str | None = None
    active: bool | None = None


class DowntimeCreate(StrictModel):
    machine_id: str
    start_time: datetime
    end_time: datetime
    reason: str = Field(default="Planned maintenance", max_length=160)
    event_type: str = Field(default="planned_maintenance", max_length=40)

    @model_validator(mode="after")
    def valid_period(self):
        _aware(self.start_time)
        _aware(self.end_time)
        if self.end_time <= self.start_time:
            raise ValueError("Downtime end_time must follow start_time")
        return self


class MachineOptionInput(StrictModel):
    machine_id: str
    processing_minutes: int = Field(gt=0, le=10080)


class OperationInput(StrictModel):
    operation_number: int = Field(gt=0, le=10000)
    operation_name: str = Field(min_length=1, max_length=120)
    setup_group: str = Field(default="", max_length=80)
    options: list[MachineOptionInput] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_options(self):
        machine_ids = [item.machine_id for item in self.options]
        if len(machine_ids) != len(set(machine_ids)):
            raise ValueError("An operation may list each machine only once")
        return self


def validate_operations(operations: list[OperationInput]) -> None:
    numbers = [item.operation_number for item in operations]
    if len(numbers) != len(set(numbers)):
        raise ValueError("Operation numbers must be unique within a job")


class JobCreate(StrictModel):
    facility_id: str
    job_number: str = Field(min_length=1, max_length=60)
    customer_reference: str = Field(default="", max_length=120)
    product_name: str = Field(min_length=1, max_length=160)
    quantity: int = Field(gt=0, le=2_147_483_647)
    priority: int = Field(default=1, ge=1, le=10)
    release_time: datetime
    due_time: datetime
    status: Literal["planned", "released", "on_hold", "completed", "cancelled"] = "planned"
    operations: list[OperationInput] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def valid_job(self):
        _aware(self.release_time)
        _aware(self.due_time)
        if self.due_time <= self.release_time:
            raise ValueError("Due time must follow release time")
        validate_operations(self.operations)
        return self


class JobUpdate(StrictModel):
    job_number: str | None = Field(default=None, min_length=1, max_length=60)
    customer_reference: str | None = Field(default=None, max_length=120)
    product_name: str | None = Field(default=None, min_length=1, max_length=160)
    quantity: int | None = Field(default=None, gt=0, le=2_147_483_647)
    priority: int | None = Field(default=None, ge=1, le=10)
    release_time: datetime | None = None
    due_time: datetime | None = None
    status: Literal["planned", "released", "on_hold", "completed", "cancelled"] | None = None
    operations: list[OperationInput] | None = Field(default=None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def valid_fields(self):
        if self.release_time is not None:
            _aware(self.release_time)
        if self.due_time is not None:
            _aware(self.due_time)
        if self.operations is not None:
            validate_operations(self.operations)
        return self


class ScheduleCreate(StrictModel):
    facility_id: str
    name: str = Field(default="", max_length=140)
    algorithm: Literal["fcfs", "spt", "edd", "cp_sat"] = "fcfs"
    objective: Literal["makespan", "total_tardiness", "weighted_tardiness", "balanced"] = "makespan"
    horizon_hours: int = Field(default=336, ge=1, le=24 * 30)
    scenario_id: str | None = None
    job_ids: list[str] | None = None
    time_limit_seconds: float = Field(
        default_factory=lambda: float(os.getenv("LINEPILOT_SOLVER_TIME_LIMIT_SECONDS", "20")), ge=0.1, le=120
    )
    random_seed: int = Field(default=42, ge=0, le=2**31 - 1)
    search_workers: int = Field(default=1, ge=1, le=8, strict=True)

    @field_validator("job_ids")
    @classmethod
    def unique_job_ids(cls, values: list[str] | None) -> list[str] | None:
        if values is not None and (len(values) > 200 or len(values) != len(set(values))):
            raise ValueError("Select at most 200 unique jobs")
        return values


class ScenarioCreate(StrictModel):
    facility_id: str
    name: str = Field(min_length=1, max_length=140)
    description: str = Field(default="", max_length=2000)
    baseline_schedule_id: str | None = None
    modifications: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class ScenarioUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=140)
    description: str | None = Field(default=None, max_length=2000)
    modifications: list[dict[str, Any]] | None = Field(default=None, max_length=100)


class ScenarioEvaluate(StrictModel):
    algorithm: Literal["fcfs", "spt", "edd", "cp_sat"] = "fcfs"
    objective: Literal["makespan", "total_tardiness", "weighted_tardiness", "balanced"] = "makespan"
    horizon_hours: int = Field(default=336, ge=1, le=24 * 30)
    time_limit_seconds: float = Field(
        default_factory=lambda: float(os.getenv("LINEPILOT_SOLVER_TIME_LIMIT_SECONDS", "20")), ge=0.1, le=120
    )
    random_seed: int = Field(default=42, ge=0, le=2**31 - 1)
    search_workers: int = Field(default=1, ge=1, le=8, strict=True)


class SimulationCreate(StrictModel):
    schedule_run_id: str
    mode: Literal["playback", "stochastic"] = "playback"
    random_seed: int = Field(default=42, ge=0, le=2**31 - 1)
    replications: int = Field(default=1, ge=1, le=30)
    processing_time_distribution: Literal["fixed", "triangular", "lognormal"] = "fixed"
    processing_time_variability: float = Field(default=0.1, ge=0, le=1)
    failure_rate_per_hour: float = Field(default=0, ge=0, le=2)
    mean_repair_minutes: float = Field(default=60, gt=0, le=10080)
    horizon_minutes: int | None = Field(default=None, ge=1, le=24 * 60 * 30)


class ImportRequest(StrictModel):
    facility_id: str
    category: Literal["jobs", "operations", "machines", "downtime"]
    csv_text: str = Field(min_length=1)
    file_name: str = Field(default="import.csv", max_length=160)

    @model_validator(mode="after")
    def size_and_name(self):
        if len(self.csv_text.encode("utf-8")) > 2_000_000:
            raise ValueError("CSV is limited to 2 MB")
        if PurePath(self.file_name).name != self.file_name or "\\" in self.file_name:
            raise ValueError("file_name must not contain a path")
        return self


class ReportCreate(StrictModel):
    schedule_run_id: str
    report_type: Literal[
        "schedule_summary",
        "optimization_comparison",
        "bottleneck_analysis",
        "simulation_results",
        "resource_utilization",
        "scenario_comparison",
    ] = "schedule_summary"
    title: str | None = Field(default=None, max_length=160)
    comparison_schedule_id: str | None = None
    simulation_run_id: str | None = None
