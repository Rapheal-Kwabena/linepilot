"""Stable JSON views of persisted records for the web application."""

from __future__ import annotations

import hashlib
import json

from app.models import (
    DowntimeEvent,
    Facility,
    ImportBatch,
    Machine,
    OperatingCalendar,
    ProductionJob,
    Report,
    Scenario,
    ScheduleRun,
    SimulationEvent,
    SimulationRun,
    WorkCenter,
)
from app.snapshot import absolute_time, iso_utc, minute_offset, parse_aware


def serialize_workspace(workspace) -> dict:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "description": workspace.description,
        "timezone": workspace.timezone,
        "settings": workspace.settings,
        "created_at": iso_utc(workspace.created_at),
    }


def serialize_facility(facility: Facility) -> dict:
    return {
        "id": facility.id,
        "workspace_id": facility.workspace_id,
        "facility_code": facility.facility_code,
        "facility_name": facility.facility_name,
        "location_label": facility.location_label,
        "timezone": facility.timezone,
        "operating_calendar_id": facility.operating_calendar_id,
        "status": facility.status,
        "sample_data": facility.sample_data,
        "created_at": iso_utc(facility.created_at),
    }


def serialize_work_center(center: WorkCenter) -> dict:
    return {
        "id": center.id,
        "facility_id": center.facility_id,
        "code": center.code,
        "name": center.name,
        "description": center.description,
        "display_order": center.display_order,
        "layout_x": center.layout_x,
        "layout_y": center.layout_y,
    }


def serialize_calendar(calendar: OperatingCalendar) -> dict:
    return {
        "id": calendar.id,
        "name": calendar.name,
        "timezone": calendar.timezone,
        "weekly_shifts": calendar.weekly_shifts,
        "holiday_exceptions": calendar.holiday_exceptions,
        "special_closures": calendar.special_closures,
    }


def serialize_downtime(event: DowntimeEvent) -> dict:
    return {
        "id": event.id,
        "machine_id": event.machine_id,
        "start_time": iso_utc(event.start_time),
        "end_time": iso_utc(event.end_time),
        "reason": event.reason,
        "event_type": event.event_type,
    }


def serialize_machine(machine: Machine) -> dict:
    return {
        "id": machine.id,
        "work_center_id": machine.work_center_id,
        "machine_code": machine.machine_code,
        "machine_name": machine.machine_name,
        "machine_type": machine.machine_type,
        "status": machine.status,
        "hourly_operating_cost": machine.hourly_operating_cost,
        "operating_calendar_id": machine.operating_calendar_id,
        "active": machine.active,
        "downtime": [serialize_downtime(item) for item in machine.downtime],
    }


def serialize_job(job: ProductionJob) -> dict:
    operations = [
        {
            "id": operation.id,
            "job_id": job.id,
            "operation_number": operation.operation_number,
            "operation_name": operation.operation_name,
            "required_sequence": operation.required_sequence,
            "default_processing_minutes": operation.default_processing_minutes,
            "setup_group": operation.setup_group,
            "status": operation.status,
            "options": [
                {
                    "machine_id": option.machine_id,
                    "processing_minutes": option.processing_minutes,
                }
                for option in sorted(operation.options, key=lambda item: item.processing_minutes)
            ],
        }
        for operation in job.operations
    ]
    return {
        "id": job.id,
        "facility_id": job.facility_id,
        "scenario_id": job.scenario_id,
        "job_number": job.job_number,
        "customer_reference": job.customer_reference,
        "product_name": job.product_name,
        "quantity": job.quantity,
        "priority": job.priority,
        "release_time": iso_utc(job.release_time),
        "due_time": iso_utc(job.due_time),
        "status": job.status,
        "operations": operations,
        "operation_count": len(operations),
        "estimated_processing_minutes": sum(op["default_processing_minutes"] for op in operations),
        "created_at": iso_utc(job.created_at),
        "updated_at": iso_utc(job.updated_at),
    }


def serialize_assignment(assignment, snapshot: dict) -> dict:
    origin = snapshot["origin"]
    origin_dt = parse_aware(origin)
    operation_to_job = {
        operation["id"]: job["id"] for job in snapshot["jobs"] for operation in job["operations"]
    }
    start_min = minute_offset(assignment.start_time, origin_dt)
    end_min = minute_offset(assignment.end_time, origin_dt)
    return {
        "id": assignment.id,
        "operation_id": assignment.operation_id,
        "job_id": operation_to_job.get(assignment.operation_id),
        "machine_id": assignment.machine_id,
        "start_time": iso_utc(assignment.start_time),
        "end_time": iso_utc(assignment.end_time),
        "start_min": start_min,
        "end_min": end_min,
        "sequence_position": assignment.sequence_position,
    }


def serialize_schedule(run: ScheduleRun, *, include_assignments: bool = True) -> dict:
    fingerprint = hashlib.sha256(
        json.dumps(run.input_snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    data = {
        "id": run.id,
        "facility_id": run.facility_id,
        "scenario_id": run.scenario_id,
        "name": run.name,
        "algorithm": run.algorithm,
        "objective": run.objective,
        "solver_configuration": run.solver_configuration or {},
        "origin": run.input_snapshot.get("origin"),
        "horizon_minutes": run.input_snapshot.get("horizon_minutes"),
        "input_fingerprint": fingerprint,
        "status": run.status,
        "solver_status": run.solver_status,
        "created_at": iso_utc(run.created_at),
        "started_at": iso_utc(run.started_at) if run.started_at else None,
        "completed_at": iso_utc(run.completed_at) if run.completed_at else None,
        "duration_ms": run.duration_ms,
        "objective_value": run.objective_value,
        "metrics": run.metrics or {},
        "diagnostics": run.diagnostics or [],
        "assignment_count": len(run.assignments),
    }
    if include_assignments:
        data["assignments"] = [serialize_assignment(item, run.input_snapshot) for item in run.assignments]
        data["snapshot"] = run.input_snapshot
    return data


def serialize_scenario(scenario: Scenario) -> dict:
    return {
        "id": scenario.id,
        "facility_id": scenario.facility_id,
        "name": scenario.name,
        "description": scenario.description,
        "baseline_schedule_id": scenario.baseline_schedule_id,
        "origin": scenario.input_snapshot.get("origin"),
        "horizon_minutes": scenario.input_snapshot.get("horizon_minutes"),
        "modifications": scenario.modifications,
        "created_at": iso_utc(scenario.created_at),
        "updated_at": iso_utc(scenario.updated_at),
    }


def serialize_simulation_event(event: SimulationEvent, origin: str) -> dict:
    return {
        "id": event.id,
        "sequence": event.sequence,
        "time_min": event.simulation_time,
        "time": absolute_time(origin, event.simulation_time),
        "event_type": event.event_type,
        "job_id": event.job_id,
        "operation_id": event.operation_id,
        "machine_id": event.machine_id,
        "payload": event.payload,
    }


def serialize_simulation(run: SimulationRun, *, schedule: ScheduleRun, include_events: bool = True) -> dict:
    data = {
        "id": run.id,
        "scenario_id": run.scenario_id,
        "schedule_run_id": run.schedule_run_id,
        "random_seed": run.random_seed,
        "configuration": run.configuration,
        "status": run.status,
        "metrics": run.summary_metrics or {},
        "diagnostics": run.diagnostics or [],
        "created_at": iso_utc(run.created_at),
        "started_at": iso_utc(run.started_at) if run.started_at else None,
        "completed_at": iso_utc(run.completed_at) if run.completed_at else None,
        "event_count": len(run.events),
    }
    if include_events:
        data["events"] = [
            serialize_simulation_event(event, schedule.input_snapshot["origin"]) for event in run.events
        ]
    return data


def serialize_import(batch: ImportBatch) -> dict:
    return {
        "id": batch.id,
        "facility_id": batch.facility_id,
        "file_name": batch.file_name,
        "category": batch.category,
        "detected_columns": batch.detected_columns,
        "import_status": batch.import_status,
        "validation_errors": batch.validation_errors,
        "rows_processed": batch.rows_processed,
        "rows_failed": batch.rows_failed,
        "created_at": iso_utc(batch.created_at),
    }


def serialize_report(report: Report) -> dict:
    return {
        "id": report.id,
        "status": "completed",
        "schedule_run_id": report.schedule_run_id,
        "report_type": report.report_type,
        "title": report.title,
        "configuration": report.configuration,
        "created_at": iso_utc(report.created_at),
        "download_url_csv": f"/api/v1/reports/{report.id}/download?format=csv",
        "download_url_pdf": f"/api/v1/reports/{report.id}/download?format=pdf",
    }
