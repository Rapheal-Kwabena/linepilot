"""Persist scenario-only capacity and surge orders without changing baseline data."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Machine, OperationMachineOption, ProductionJob, ProductionOperation, Scenario
from app.snapshot import parse_aware, scenario_job_id, scenario_machine_id


def clear_virtual_entities(db: Session, scenario_id: str) -> None:
    """Only call before a scenario has persisted runs."""
    for job in db.scalars(select(ProductionJob).where(ProductionJob.scenario_id == scenario_id)).all():
        db.delete(job)
    db.flush()
    for machine in db.scalars(select(Machine).where(Machine.scenario_id == scenario_id)).all():
        db.delete(machine)
    db.flush()


def materialize_virtual_entities(
    db: Session,
    scenario: Scenario,
    modified_snapshot: dict,
) -> None:
    """Create FK targets for virtual machines/operations emitted by a scenario."""
    base = scenario.input_snapshot
    base_machine_ids = {item["id"] for item in base["machines"]}
    base_job_ids = {item["id"] for item in base["jobs"]}
    machine_snapshot = {item["id"]: item for item in modified_snapshot["machines"]}
    job_snapshot = {item["id"]: item for item in modified_snapshot["jobs"]}

    # Machines are inserted first because surge orders can list their options.
    for index, modification in enumerate(scenario.modifications):
        if modification.get("type") != "add_capacity":
            continue
        new_id = scenario_machine_id(scenario.id, index)
        if new_id in base_machine_ids or db.get(Machine, new_id):
            continue
        source = db.get(Machine, modification["source_machine_id"])
        spec = machine_snapshot[new_id]
        db.add(
            Machine(
                id=new_id,
                scenario_id=scenario.id,
                work_center_id=source.work_center_id,
                machine_code=spec["code"],
                machine_name=f"{source.machine_name} — scenario capacity",
                machine_type=source.machine_type,
                status="available",
                hourly_operating_cost=spec["hourly_cost"],
                operating_calendar_id=source.operating_calendar_id,
                active=True,
            )
        )
    db.flush()

    origin = parse_aware(base["origin"])
    for index, modification in enumerate(scenario.modifications):
        if modification.get("type") != "order_surge":
            continue
        source = db.get(ProductionJob, modification["template_job_id"])
        for number in range(modification["count"]):
            new_id = scenario_job_id(scenario.id, index, number)
            if new_id in base_job_ids or db.get(ProductionJob, new_id):
                continue
            spec = job_snapshot[new_id]
            job = ProductionJob(
                id=new_id,
                scenario_id=scenario.id,
                facility_id=scenario.facility_id,
                job_number=spec["job_number"],
                customer_reference=spec.get(
                    "customer_reference", source.customer_reference if source else ""
                ),
                product_name=spec.get("product_name", source.product_name if source else spec["job_number"]),
                quantity=spec.get("quantity", source.quantity if source else 1),
                priority=spec["priority"],
                release_time=origin + timedelta(minutes=spec["release_min"]),
                due_time=origin + timedelta(minutes=spec["due_min"]),
                status="planned",
            )
            db.add(job)
            for operation_spec in spec["operations"]:
                operation = ProductionOperation(
                    id=operation_spec["id"],
                    job=job,
                    operation_number=operation_spec["operation_number"],
                    operation_name=operation_spec["name"],
                    required_sequence=operation_spec["operation_number"],
                    default_processing_minutes=min(
                        option["processing_minutes"] for option in operation_spec["options"]
                    ),
                    setup_group="",
                    status="planned",
                )
                db.add(operation)
                for option in operation_spec["options"]:
                    db.add(
                        OperationMachineOption(
                            operation=operation,
                            machine_id=option["machine_id"],
                            processing_minutes=option["processing_minutes"],
                        )
                    )
    db.flush()
