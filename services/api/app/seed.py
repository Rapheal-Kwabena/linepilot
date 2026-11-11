"""Deterministic fictional Northline demonstration data.

The planning week is the next Monday in the facility timezone unless
DEMO_ANCHOR_DATE (YYYY-MM-DD, a Monday) is set. Existing data is never reset.
"""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime, time, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import Base, SessionLocal, engine
from app.models import (
    DowntimeEvent,
    Facility,
    Machine,
    OperatingCalendar,
    OperationMachineOption,
    ProductionJob,
    ProductionOperation,
    WorkCenter,
    Workspace,
)

ZONE = ZoneInfo("America/Chicago")


def demo_id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"linepilot-demo:{key}"))


def anchor_date(today: date | None = None) -> date:
    configured = os.getenv("DEMO_ANCHOR_DATE")
    if configured:
        selected = date.fromisoformat(configured)
        if selected.weekday() != 0:
            raise ValueError("DEMO_ANCHOR_DATE must be a Monday")
        return selected
    current = today or datetime.now(ZONE).date()
    return current + timedelta(days=(7 - current.weekday()) % 7)


def local_at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE).astimezone(timezone.utc)


def demo_spec(job_count: int = 20, machine_count: int = 6, *, anchor: date | None = None) -> dict:
    """Generate repeatable production data for a chosen planning Monday."""
    if machine_count < 6 or job_count < 1:
        raise ValueError("Demo needs at least six machines and one job")
    week = anchor or anchor_date()
    work_centers = [
        {"code": "CUT", "name": "Cutting", "layout_x": 0, "layout_y": 0},
        {"code": "FORM", "name": "Forming", "layout_x": 1, "layout_y": 0},
        {"code": "FIN", "name": "Finishing", "layout_x": 2, "layout_y": 0},
    ]
    machines = []
    for index in range(machine_count):
        center = work_centers[index % 3]["code"]
        machines.append(
            {
                "code": f"M-{index + 1:02d}" if machine_count == 6 else f"L-{index + 1:02d}",
                "name": f"{center.title()} Cell {index // 3 + 1}",
                "center": center,
                "type": {"CUT": "laser cutting", "FORM": "press forming", "FIN": "inspection and finish"}[
                    center
                ],
                "cost": float(46 + (index * 13) % 37),
            }
        )
    center_machines = {
        center["code"]: [machine for machine in machines if machine["center"] == center["code"]]
        for center in work_centers
    }
    products = ["Valve bracket", "Pump housing", "Guide plate", "Bearing cover", "Mounting rail"]
    jobs = []
    for index in range(job_count):
        release_day = week + timedelta(days=index // (30 if job_count > 20 else 10))
        release_hour = 6 + ((index * 3) % 5)
        release = local_at(release_day, release_hour)
        # A repeatable mix of same-day and later deadlines creates real
        # dispatching tradeoffs without making every order automatically late.
        if index % 4 in (0, 1):
            due_day = release_day
            due_hour = max(release_hour + 2, 12 + (index * 3) % 8)
        else:
            due_day = release_day + timedelta(days=1 + (index % 3 == 0))
            due_hour = 9 + (index % 8)
        due = local_at(due_day, due_hour)
        operations = []
        for op_index, center in enumerate(work_centers):
            base_duration = 37 + (index * 11 + op_index * 19) % 71
            options = [
                {
                    "machine_code": machine["code"],
                    "processing_minutes": base_duration + option_index * (4 + index % 7),
                }
                for option_index, machine in enumerate(center_machines[center["code"]])
            ]
            operations.append(
                {
                    "operation_number": (op_index + 1) * 10,
                    "operation_name": ["Cut blank", "Form component", "Finish and inspect"][op_index],
                    "options": options,
                }
            )
        jobs.append(
            {
                "job_number": f"JOB-{index + 101:03d}" if job_count == 20 else f"LOAD-{index + 1:03d}",
                "customer_reference": f"PO-{2401 + index:04d}",
                "product_name": products[index % len(products)],
                "quantity": 8 + (index * 7) % 41,
                "priority": 1 + (index * 3) % 4,
                "release_time": release,
                "due_time": due,
                "operations": operations,
            }
        )
    return {"anchor": week, "work_centers": work_centers, "machines": machines, "jobs": jobs}


def seed_facility(db: Session, *, large: bool = False, anchor: date | None = None) -> Facility:
    code = "NORTHLINE-LARGE" if large else "NORTHLINE"
    existing = db.scalar(select(Facility).where(Facility.facility_code == code))
    if existing:
        return existing

    spec = demo_spec(100 if large else 20, 10 if large else 6, anchor=anchor)
    namespace = "large" if large else "standard"
    workspace = db.get(Workspace, demo_id("workspace"))
    if workspace is None:
        workspace = Workspace(
            id=demo_id("workspace"),
            name="Northline Demo Workspace",
            description="Fictional sample manufacturing data for engineering exploration.",
            timezone="America/Chicago",
            settings={"demo": True},
        )
        db.add(workspace)

    calendar = OperatingCalendar(
        id=demo_id(f"{namespace}:calendar"),
        name="Northline Standard Shifts",
        timezone="America/Chicago",
        weekly_shifts=[{"day": day, "start": "06:00", "end": "22:00"} for day in range(5)]
        + [{"day": 5, "start": "06:00", "end": "14:00"}],
        holiday_exceptions=[],
        special_closures=[],
    )
    db.add(calendar)
    facility = Facility(
        id=demo_id(f"{namespace}:facility"),
        workspace=workspace,
        facility_code=code,
        facility_name="Northline Components Plant" if not large else "Northline Load Test Plant",
        location_label="Fictional sample facility",
        timezone="America/Chicago",
        operating_calendar=calendar,
        status="active",
        sample_data=True,
    )
    db.add(facility)

    centers: dict[str, WorkCenter] = {}
    for order, center_spec in enumerate(spec["work_centers"]):
        center = WorkCenter(
            id=demo_id(f"{namespace}:center:{center_spec['code']}"),
            facility=facility,
            code=center_spec["code"],
            name=center_spec["name"],
            description=f"{center_spec['name']} operations",
            display_order=order,
            layout_x=center_spec["layout_x"],
            layout_y=center_spec["layout_y"],
        )
        db.add(center)
        centers[center_spec["code"]] = center

    machines: dict[str, Machine] = {}
    for machine_spec in spec["machines"]:
        machine = Machine(
            id=demo_id(f"{namespace}:machine:{machine_spec['code']}"),
            work_center=centers[machine_spec["center"]],
            machine_code=machine_spec["code"],
            machine_name=machine_spec["name"],
            machine_type=machine_spec["type"],
            status="available",
            hourly_operating_cost=machine_spec["cost"],
            operating_calendar=calendar,
            active=True,
        )
        db.add(machine)
        machines[machine_spec["code"]] = machine

    # Real planned maintenance in the fictional input, not invented analytics.
    maintenance_code = "L-03" if large else "M-03"
    db.add(
        DowntimeEvent(
            id=demo_id(f"{namespace}:maintenance"),
            machine=machines[maintenance_code],
            start_time=local_at(spec["anchor"], 11),
            end_time=local_at(spec["anchor"], 14),
            reason="Scheduled press inspection",
            event_type="planned_maintenance",
        )
    )

    for job_spec in spec["jobs"]:
        job = ProductionJob(
            id=demo_id(f"{namespace}:job:{job_spec['job_number']}"),
            facility=facility,
            job_number=job_spec["job_number"],
            customer_reference=job_spec["customer_reference"],
            product_name=job_spec["product_name"],
            quantity=job_spec["quantity"],
            priority=job_spec["priority"],
            release_time=job_spec["release_time"],
            due_time=job_spec["due_time"],
            status="planned",
        )
        db.add(job)
        for operation_spec in job_spec["operations"]:
            number = operation_spec["operation_number"]
            operation = ProductionOperation(
                id=demo_id(f"{namespace}:operation:{job_spec['job_number']}:{number}"),
                job=job,
                operation_number=number,
                operation_name=operation_spec["operation_name"],
                required_sequence=number,
                default_processing_minutes=min(
                    option["processing_minutes"] for option in operation_spec["options"]
                ),
                setup_group="",
                status="planned",
            )
            db.add(operation)
            for option_spec in operation_spec["options"]:
                db.add(
                    OperationMachineOption(
                        operation=operation,
                        machine=machines[option_spec["machine_code"]],
                        processing_minutes=option_spec["processing_minutes"],
                    )
                )
    db.commit()
    return facility


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed fictional Northline demonstration data")
    parser.add_argument(
        "--large", action="store_true", help="Also create a 10-machine, 100-job load test facility"
    )
    args = parser.parse_args()
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        facility = seed_facility(db)
        print(f"Seeded or found {facility.facility_name} ({facility.facility_code})")
        if args.large:
            large = seed_facility(db, large=True)
            print(f"Seeded or found {large.facility_name} ({large.facility_code})")


if __name__ == "__main__":
    main()
