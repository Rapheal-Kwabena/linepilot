"""Reproducible CSV and PDF engineering reports from stored run records."""

from __future__ import annotations

import csv
import io
from datetime import timezone
from html import escape

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.http import get_or_404, invalid
from app.models import Facility, Report, Scenario, ScheduleRun, SimulationRun
from app.schemas import ReportCreate
from app.serializers import serialize_assignment, serialize_report

router = APIRouter()

METRIC_LABELS = {
    "makespan_minutes": "Makespan (min)",
    "completed_jobs": "Completed jobs",
    "late_jobs": "Late jobs",
    "total_tardiness_minutes": "Total tardiness (min)",
    "total_weighted_tardiness_minutes": "Weighted tardiness (weighted min)",
    "on_time_delivery_rate": "On-time delivery rate (%)",
    "throughput_jobs_per_hour": "Throughput (jobs/h)",
    "mean_job_waiting_minutes": "Mean job waiting (min)",
    "average_machine_utilization": "Average machine utilization (%)",
    "operating_cost": "Configured machine operating cost",
}

DEFINITIONS = [
    "Makespan: final operation completion minus schedule origin.",
    "Tardiness: positive delay beyond a job's due time; zero when on time.",
    "Utilization: busy production time divided by available calendar time after planned downtime.",
    "Operating cost includes configured machine busy-time costs only; no labor or savings claim is implied.",
]
PERCENTAGE_METRICS = {"on_time_delivery_rate", "average_machine_utilization"}


def _format(value) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _format_metric(key: str, value, *, difference: bool = False) -> str:
    if key in PERCENTAGE_METRICS and isinstance(value, (int, float)):
        return f"{value * 100:.1f}{' pp' if difference else '%'}"
    return _format(value)


def _solver_settings(run: ScheduleRun) -> str:
    if run.algorithm != "cp_sat":
        return "Dispatch rule; CP-SAT search settings do not apply"
    config = run.solver_configuration or {}
    if not config:
        return "Not recorded for this run"
    return (
        f"Random seed {config.get('random_seed', 'N/A')}; "
        f"CP-SAT search limit {config.get('time_limit_seconds', 'N/A')} s; "
        f"search workers {config.get('search_workers', 'N/A')}"
    )


def _get_report_context(db: Session, report: Report) -> tuple[ScheduleRun, Facility, dict]:
    run = get_or_404(db, ScheduleRun, report.schedule_run_id, "Schedule")
    facility = get_or_404(db, Facility, run.facility_id, "Facility")
    context: dict = {
        "schedule": run,
        "facility": facility,
        "metrics": run.metrics,
        "chart_metrics": run.metrics,
        "chart_title": "Scheduled machine utilization / percent of available time",
        "rows": [],
        "section_title": "Schedule metrics",
        "notes": [],
    }
    if report.report_type == "schedule_summary":
        context["section_title"] = "Scheduled operations"
        context["rows"] = [["Job", "Operation", "Machine", "Start (UTC)", "End (UTC)", "Duration (min)"]]
        jobs = {job["id"]: job["job_number"] for job in run.input_snapshot["jobs"]}
        operations = {
            operation["id"]: f"{operation['operation_number']} / {operation['name']}"
            for job in run.input_snapshot["jobs"]
            for operation in job["operations"]
        }
        machines = {machine["id"]: machine["code"] for machine in run.input_snapshot["machines"]}
        for assignment in run.assignments:
            item = serialize_assignment(assignment, run.input_snapshot)
            context["rows"].append(
                [
                    jobs.get(item["job_id"], item["job_id"]),
                    operations.get(item["operation_id"], item["operation_id"][:8]),
                    machines.get(item["machine_id"], item["machine_id"]),
                    item["start_time"],
                    item["end_time"],
                    item["end_min"] - item["start_min"],
                ]
            )
    elif report.report_type == "optimization_comparison":
        comparison_id = report.configuration.get("comparison_schedule_id")
        other = get_or_404(db, ScheduleRun, comparison_id, "Comparison schedule")
        context["section_title"] = f"Compared with {other.algorithm.upper()} run {other.id[:8]}"
        context["rows"] = [["Metric", "Selected", "Comparison", "Difference"]]
        for key, label in METRIC_LABELS.items():
            a = run.metrics.get(key)
            b = other.metrics.get(key)
            difference = a - b if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
            context["rows"].append(
                [
                    label,
                    _format_metric(key, a),
                    _format_metric(key, b),
                    _format_metric(key, difference, difference=True),
                ]
            )
        context["notes"].append("The two schedules use the same stored engineering input snapshot.")
    elif report.report_type == "bottleneck_analysis":
        from app.api.runs import _assignment_inputs
        from app.engineering import analyze_bottlenecks

        analysis = analyze_bottlenecks(run.input_snapshot, _assignment_inputs(run))
        context["section_title"] = "Capacity pressure candidates"
        context["rows"] = [
            ["Machine", "Busy (min)", "Available (min)", "Utilization (%)", "Late jobs using machine"]
        ]
        for item in analysis.get("machine_rankings", []):
            context["rows"].append(
                [
                    item["machine_code"],
                    item["busy_minutes"],
                    item["available_minutes"],
                    _format_metric("average_machine_utilization", item["utilization"]),
                    item["late_jobs_using_machine"],
                ]
            )
        context["notes"].extend(
            finding["title"] + ": " + finding["interpretation"] for finding in analysis.get("findings", [])
        )
    elif report.report_type == "resource_utilization":
        context["section_title"] = "Machine resource utilization"
        context["rows"] = [
            ["Machine", "Busy (min)", "Available (min)", "Idle (min)", "Downtime (min)", "Utilization (%)"]
        ]
        for item in run.metrics.get("machine_metrics", []):
            context["rows"].append(
                [
                    item["machine_code"],
                    item["busy_minutes"],
                    item["available_minutes"],
                    item["idle_minutes"],
                    item["downtime_minutes"],
                    _format_metric("average_machine_utilization", item["utilization"]),
                ]
            )
    elif report.report_type == "simulation_results":
        simulation = get_or_404(
            db, SimulationRun, report.configuration.get("simulation_run_id"), "Simulation"
        )
        context["section_title"] = "Simulation metrics"
        if simulation.summary_metrics.get("machine_metrics"):
            context["chart_metrics"] = simulation.summary_metrics
            context["chart_title"] = "Simulated machine utilization / first replication"
        context["rows"] = [["Metric", "Observed value"]]
        for key, value in simulation.summary_metrics.items():
            if isinstance(value, (int, float, str)) or value is None:
                context["rows"].append([key.replace("_", " ").title(), _format(value)])
        context["notes"].append(
            f"Simulation mode: {simulation.configuration.get('mode')}; seed: {simulation.random_seed}; replications: {simulation.configuration.get('replications')}"
        )
        if (simulation.configuration.get("replications") or 1) > 1:
            context["notes"].append(
                "Scalar simulation metrics are replication means; machine utilization chart uses the first saved replication."
            )
        intervals = simulation.summary_metrics.get("confidence_intervals") or {}
        for key, interval in intervals.items():
            context["notes"].append(f"{key.replace('_', ' ').title()} uncertainty interval: {interval}")
    elif report.report_type == "scenario_comparison":
        scenario = get_or_404(db, Scenario, run.scenario_id, "Scenario")
        baseline = get_or_404(db, ScheduleRun, scenario.baseline_schedule_id, "Baseline schedule")
        context["section_title"] = f"Scenario: {scenario.name}"
        context["rows"] = [["Metric", "Baseline", "Scenario", "Difference"]]
        for key, label in METRIC_LABELS.items():
            a = baseline.metrics.get(key)
            b = run.metrics.get(key)
            difference = b - a if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None
            context["rows"].append(
                [
                    label,
                    _format_metric(key, a),
                    _format_metric(key, b),
                    _format_metric(key, difference, difference=True),
                ]
            )
        context["notes"].append(f"Scenario modifications: {scenario.modifications}")
    return run, facility, context


@router.post("/reports", status_code=201)
def create_report(body: ReportCreate, db: Session = Depends(get_db)) -> dict:
    run = get_or_404(db, ScheduleRun, body.schedule_run_id, "Schedule")
    if run.status != "completed":
        raise invalid("Reports require a completed feasible schedule")
    configuration = {}
    if body.report_type == "optimization_comparison":
        if not body.comparison_schedule_id:
            raise invalid("comparison_schedule_id is required for an optimization comparison")
        other = get_or_404(db, ScheduleRun, body.comparison_schedule_id, "Comparison schedule")
        if other.status != "completed" or other.facility_id != run.facility_id:
            raise invalid("Comparison schedule must be completed in the same facility")
        from app.serializers import serialize_schedule

        if (
            serialize_schedule(other, include_assignments=False)["input_fingerprint"]
            != serialize_schedule(run, include_assignments=False)["input_fingerprint"]
        ):
            raise invalid("Optimization comparison requires identical input snapshots")
        configuration["comparison_schedule_id"] = other.id
    if body.report_type == "simulation_results":
        if not body.simulation_run_id:
            raise invalid("simulation_run_id is required for simulation results")
        simulation = get_or_404(db, SimulationRun, body.simulation_run_id, "Simulation")
        if simulation.schedule_run_id != run.id or simulation.status != "completed":
            raise invalid("Simulation must be completed for this schedule")
        configuration["simulation_run_id"] = simulation.id
    if body.report_type == "scenario_comparison":
        if not run.scenario_id:
            raise invalid("Selected schedule is not a scenario evaluation")
        scenario = get_or_404(db, Scenario, run.scenario_id, "Scenario")
        if not scenario.baseline_schedule_id:
            raise invalid("Scenario has no baseline schedule")
        configuration["baseline_schedule_id"] = scenario.baseline_schedule_id
    title = body.title or body.report_type.replace("_", " ").title()
    report = Report(
        schedule_run_id=run.id, report_type=body.report_type, title=title, configuration=configuration
    )
    db.add(report)
    db.commit()
    return serialize_report(report)


@router.get("/reports")
def list_reports(
    facility_id: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    stmt = select(Report).join(ScheduleRun)
    if facility_id:
        stmt = stmt.where(ScheduleRun.facility_id == facility_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(Report.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return {
        "items": [serialize_report(item) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/reports/{report_id}")
def get_report(report_id: str, db: Session = Depends(get_db)) -> dict:
    return serialize_report(get_or_404(db, Report, report_id, "Report"))


def _safe_csv(value) -> str:
    text = _format(value)
    return "'" + text if isinstance(value, str) and text.lstrip().startswith(("=", "+", "-", "@")) else text


def _utilization_chart(metrics: dict) -> Drawing | None:
    rows = sorted(metrics.get("machine_metrics") or [], key=lambda item: item.get("machine_code", ""))[:12]
    if not rows:
        return None
    width = 9.8 * inch
    row_height = 20
    height = 36 + len(rows) * row_height
    drawing = Drawing(width, height)
    left = 85
    plot_width = width - 185
    bottom = 16
    for tick in (0, 0.25, 0.5, 0.75, 1.0):
        x = left + plot_width * tick
        drawing.add(Line(x, bottom, x, height - 18, strokeColor=colors.HexColor("#E9EBED"), strokeWidth=0.5))
        drawing.add(
            String(
                x,
                2,
                f"{tick * 100:.0f}%",
                fontName="Helvetica",
                fontSize=7,
                fillColor=colors.HexColor("#626B73"),
            )
        )
    for index, row in enumerate(rows):
        y = height - 28 - index * row_height
        code = str(row.get("machine_code") or str(row.get("machine_id", ""))[:8])
        drawing.add(
            String(0, y, code, fontName="Helvetica-Bold", fontSize=8, fillColor=colors.HexColor("#171C22"))
        )
        value = row.get("utilization")
        if isinstance(value, (int, float)):
            fraction = max(0, min(1, value))
            color = colors.HexColor("#E77735") if fraction >= 0.85 else colors.HexColor("#2155CD")
            drawing.add(
                Rect(left, y - 1, max(1, plot_width * fraction), 9, fillColor=color, strokeColor=None)
            )
            label = f"{value * 100:.1f}%"
        else:
            label = "N/A"
        drawing.add(
            String(
                left + plot_width + 8,
                y,
                label,
                fontName="Helvetica",
                fontSize=8,
                fillColor=colors.HexColor("#171C22"),
            )
        )
    return drawing


def _csv_content(report: Report, run: ScheduleRun, facility: Facility, context: dict) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["LINEPILOT", _safe_csv(report.title)])
    writer.writerow(["Facility", _safe_csv(facility.facility_name)])
    writer.writerow(
        ["Data source", "Fictional sample data" if facility.sample_data else "Persisted production data"]
    )
    writer.writerow(["Schedule run", run.id])
    writer.writerow(["Algorithm", run.algorithm])
    writer.writerow(["Objective", run.objective])
    writer.writerow(["Solver settings", _solver_settings(run)])
    writer.writerow(["Solver status", run.solver_status])
    writer.writerow(["Schedule origin (UTC)", run.input_snapshot["origin"]])
    writer.writerow(["Generated (UTC)", report.created_at.astimezone(timezone.utc).isoformat()])
    writer.writerow([])
    writer.writerow(["Metrics"])
    for key, label in METRIC_LABELS.items():
        value = run.metrics.get(key)
        csv_value = (
            f"{value * 100:.1f}"
            if key in PERCENTAGE_METRICS and isinstance(value, (int, float))
            else _safe_csv(value)
        )
        writer.writerow([label, csv_value])
    writer.writerow([])
    writer.writerow([context["section_title"]])
    for row in context["rows"]:
        writer.writerow([_safe_csv(value) for value in row])
    writer.writerow([])
    writer.writerow(["Definitions and limitations"])
    for note in DEFINITIONS + context["notes"]:
        writer.writerow([_safe_csv(note)])
    return output.getvalue().encode("utf-8-sig")


def _pdf_content(report: Report, run: ScheduleRun, facility: Facility, context: dict) -> bytes:
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=(11 * inch, 8.5 * inch),
        leftMargin=0.55 * inch,
        rightMargin=0.55 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "LinePilotTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=21,
        leading=23,
        textColor=colors.HexColor("#171C22"),
        alignment=TA_LEFT,
    )
    small = ParagraphStyle("LinePilotSmall", parent=styles["BodyText"], fontSize=8, leading=11)
    story = [Paragraph(escape(report.title.upper()), title_style), Spacer(1, 0.12 * inch)]
    metadata = [
        ["Facility", facility.facility_name],
        ["Source", "Fictional sample data" if facility.sample_data else "Persisted production data"],
        ["Schedule", run.id],
        ["Algorithm / objective", f"{run.algorithm.upper()} / {run.objective}"],
        ["Solver settings", _solver_settings(run)],
        ["Solver status", run.solver_status or "N/A"],
        ["Origin (UTC)", run.input_snapshot["origin"]],
        ["Generated (UTC)", report.created_at.astimezone(timezone.utc).isoformat()],
    ]
    meta_table = Table(metadata, colWidths=[1.35 * inch, 8.45 * inch])
    meta_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LINEBELOW", (0, -1), (-1, -1), 0.7, colors.HexColor("#D7DBDE")),
            ]
        )
    )
    story.extend([meta_table, Spacer(1, 0.2 * inch), Paragraph("Calculated metrics", styles["Heading2"])])
    metric_rows = [["Metric", "Value"]] + [
        [label, _format_metric(key, run.metrics.get(key))] for key, label in METRIC_LABELS.items()
    ]
    metric_table = Table(metric_rows, colWidths=[4.9 * inch, 4.9 * inch], repeatRows=1)
    metric_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#171C22")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D7DBDE")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F4F0")]),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([metric_table, Spacer(1, 0.2 * inch)])
    chart = _utilization_chart(context["chart_metrics"])
    if chart is not None:
        story.append(
            KeepTogether(
                [
                    Paragraph(escape(context["chart_title"]), styles["Heading2"]),
                    chart,
                    Spacer(1, 0.16 * inch),
                ]
            )
        )
    story.append(Paragraph(escape(context["section_title"]), styles["Heading2"]))
    rows = context["rows"] or [["No detail rows"]]
    width = 9.8 * inch / len(rows[0])
    # Long identifiers and timestamps wrap rather than bleed into adjacent cells.
    table_rows = [[Paragraph(escape(str(value)), small) for value in row] for row in rows]
    detail_table = Table(table_rows, colWidths=[width] * len(rows[0]), repeatRows=1)
    detail_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EBED")),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#D7DBDE")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend(
        [detail_table, Spacer(1, 0.2 * inch), Paragraph("Definitions and limitations", styles["Heading2"])]
    )
    notes = DEFINITIONS + context["notes"]
    for index, note in enumerate(notes):
        story.append(Paragraph(escape(note), small))
        if index < len(notes) - 1:
            story.append(Spacer(1, 0.04 * inch))
    document.build(story)
    return output.getvalue()


@router.get("/reports/{report_id}/download")
def download_report(
    report_id: str,
    format: str = Query(default="pdf", pattern="^(csv|pdf)$"),
    db: Session = Depends(get_db),
) -> Response:
    report = get_or_404(db, Report, report_id, "Report")
    run, facility, context = _get_report_context(db, report)
    content = (
        _csv_content(report, run, facility, context)
        if format == "csv"
        else _pdf_content(report, run, facility, context)
    )
    return Response(
        content=content,
        media_type="text/csv" if format == "csv" else "application/pdf",
        headers={"Content-Disposition": f'attachment; filename="linepilot-{report.id}.{format}"'},
    )
