"""Normalized persisted facility, production, and engineering run records."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base, UTCDateTime


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False
    )


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), default="UTC", nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    facilities: Mapped[list[Facility]] = relationship(back_populates="workspace")


class OperatingCalendar(Base, TimestampMixin):
    __tablename__ = "operating_calendars"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    weekly_shifts: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    holiday_exceptions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    special_closures: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class Facility(Base, TimestampMixin):
    __tablename__ = "facilities"
    __table_args__ = (UniqueConstraint("workspace_id", "facility_code", name="uq_facility_code"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    facility_code: Mapped[str] = mapped_column(String(40), nullable=False)
    facility_name: Mapped[str] = mapped_column(String(160), nullable=False)
    location_label: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    operating_calendar_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("operating_calendars.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(30), default="active", nullable=False)
    sample_data: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="facilities")
    operating_calendar: Mapped[OperatingCalendar | None] = relationship()
    work_centers: Mapped[list[WorkCenter]] = relationship(back_populates="facility")
    jobs: Mapped[list[ProductionJob]] = relationship(back_populates="facility")


class WorkCenter(Base, TimestampMixin):
    __tablename__ = "work_centers"
    __table_args__ = (UniqueConstraint("facility_id", "code", name="uq_work_center_code"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    facility_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    layout_x: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    layout_y: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    facility: Mapped[Facility] = relationship(back_populates="work_centers")
    machines: Mapped[list[Machine]] = relationship(back_populates="work_center")


class Machine(Base, TimestampMixin):
    __tablename__ = "machines"
    __table_args__ = (CheckConstraint("hourly_operating_cost >= 0", name="ck_machine_cost"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    work_center_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("work_centers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    scenario_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("scenarios.id", ondelete="RESTRICT"), index=True
    )
    machine_code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    machine_name: Mapped[str] = mapped_column(String(120), nullable=False)
    machine_type: Mapped[str] = mapped_column(String(80), default="general", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="available", nullable=False)
    hourly_operating_cost: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    operating_calendar_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("operating_calendars.id", ondelete="SET NULL")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    work_center: Mapped[WorkCenter] = relationship(back_populates="machines")
    operating_calendar: Mapped[OperatingCalendar | None] = relationship()
    downtime: Mapped[list[DowntimeEvent]] = relationship(
        back_populates="machine", cascade="all, delete-orphan", order_by="DowntimeEvent.start_time"
    )


class ProductionJob(Base, TimestampMixin):
    __tablename__ = "production_jobs"
    __table_args__ = (
        UniqueConstraint("facility_id", "job_number", name="uq_job_number"),
        CheckConstraint("quantity > 0", name="ck_job_quantity"),
        CheckConstraint("priority >= 1", name="ck_job_priority"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    facility_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    scenario_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("scenarios.id", ondelete="RESTRICT"), index=True
    )
    job_number: Mapped[str] = mapped_column(String(60), nullable=False)
    customer_reference: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    product_name: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    release_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    due_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(30), default="planned", nullable=False)

    facility: Mapped[Facility] = relationship(back_populates="jobs")
    operations: Mapped[list[ProductionOperation]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="ProductionOperation.operation_number"
    )


class ProductionOperation(Base, TimestampMixin):
    __tablename__ = "production_operations"
    __table_args__ = (
        UniqueConstraint("job_id", "operation_number", name="uq_job_operation_number"),
        CheckConstraint("default_processing_minutes > 0", name="ck_default_processing_minutes"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("production_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operation_number: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_name: Mapped[str] = mapped_column(String(120), nullable=False)
    required_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    default_processing_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    setup_group: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="planned", nullable=False)

    job: Mapped[ProductionJob] = relationship(back_populates="operations")
    options: Mapped[list[OperationMachineOption]] = relationship(
        back_populates="operation", cascade="all, delete-orphan"
    )


class OperationMachineOption(Base):
    __tablename__ = "operation_machine_options"
    __table_args__ = (
        UniqueConstraint("operation_id", "machine_id", name="uq_operation_machine"),
        CheckConstraint("processing_minutes > 0", name="ck_option_processing_minutes"),
    )

    operation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("production_operations.id", ondelete="CASCADE"), primary_key=True
    )
    machine_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("machines.id", ondelete="RESTRICT"), primary_key=True
    )
    processing_minutes: Mapped[int] = mapped_column(Integer, nullable=False)

    operation: Mapped[ProductionOperation] = relationship(back_populates="options")
    machine: Mapped[Machine] = relationship()


class DowntimeEvent(Base, TimestampMixin):
    __tablename__ = "downtime_events"
    __table_args__ = (CheckConstraint("end_time > start_time", name="ck_downtime_period"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    machine_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("machines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    end_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reason: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), default="planned_maintenance", nullable=False)

    machine: Mapped[Machine] = relationship(back_populates="downtime")


class ScheduleRun(Base):
    __tablename__ = "schedule_runs"
    __table_args__ = (
        Index("ix_schedule_facility_created", "facility_id", "created_at"),
        Index("ix_schedule_execution_status_lease", "execution_backend", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    facility_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False
    )
    scenario_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("scenarios.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(140), default="", nullable=False)
    algorithm: Mapped[str] = mapped_column(String(30), nullable=False)
    objective: Mapped[str] = mapped_column(String(40), nullable=False)
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    solver_configuration: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default="{}", nullable=False
    )
    execution_backend: Mapped[str] = mapped_column(
        String(20), default="local", server_default="local", nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False, index=True)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_enqueued_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    solver_status: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    objective_value: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    diagnostics: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    assignments: Mapped[list[ScheduledOperation]] = relationship(
        back_populates="schedule_run", cascade="all, delete-orphan", order_by="ScheduledOperation.start_time"
    )


class ScheduledOperation(Base):
    __tablename__ = "scheduled_operations"
    __table_args__ = (
        UniqueConstraint("schedule_run_id", "operation_id", name="uq_scheduled_operation"),
        CheckConstraint("end_time > start_time", name="ck_scheduled_period"),
        Index("ix_scheduled_machine_start", "machine_id", "start_time"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    schedule_run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("schedule_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("production_operations.id", ondelete="RESTRICT"), nullable=False
    )
    machine_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("machines.id", ondelete="RESTRICT"), nullable=False
    )
    start_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    end_time: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    sequence_position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    schedule_run: Mapped[ScheduleRun] = relationship(back_populates="assignments")


class Scenario(Base, TimestampMixin):
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    facility_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    baseline_schedule_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    input_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    modifications: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class SimulationRun(Base):
    __tablename__ = "simulation_runs"
    __table_args__ = (
        Index("ix_simulation_execution_status_lease", "execution_backend", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    scenario_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("scenarios.id", ondelete="SET NULL")
    )
    schedule_run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("schedule_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    random_seed: Mapped[int] = mapped_column(Integer, default=42, nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    execution_backend: Mapped[str] = mapped_column(
        String(20), default="local", server_default="local", nullable=False
    )
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False, index=True)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    last_enqueued_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    summary_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    diagnostics: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    events: Mapped[list[SimulationEvent]] = relationship(
        back_populates="simulation_run",
        cascade="all, delete-orphan",
        order_by="SimulationEvent.sequence",
    )


class SimulationEvent(Base):
    __tablename__ = "simulation_events"
    __table_args__ = (
        Index("ix_simulation_event_time", "simulation_run_id", "simulation_time"),
        UniqueConstraint("simulation_run_id", "sequence", name="uq_simulation_event_sequence"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    simulation_run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False
    )
    simulation_time: Mapped[float] = mapped_column(Float, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    job_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    operation_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    machine_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    simulation_run: Mapped[SimulationRun] = relationship(back_populates="events")


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    facility_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("facilities.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    file_name: Mapped[str] = mapped_column(String(160), nullable=False)
    file_type: Mapped[str] = mapped_column(String(30), default="csv", nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    detected_columns: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    import_status: Mapped[str] = mapped_column(String(30), nullable=False)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    rows_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_workspace_time", "workspace_id", "timestamp"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    details: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=new_id)
    schedule_run_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("schedule_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    report_type: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
