"""Capacity windows and trends must use the same saved assignment intervals."""

from __future__ import annotations

import pytest

from app.engineering.utilization import compute_utilization_analysis


def _snapshot() -> dict:
    return {
        "origin": "2026-11-16T12:00:00Z",
        "horizon_minutes": 240,
        "machines": [
            {
                "id": "m1",
                "code": "M-01",
                "work_center_id": "cut",
                "availability": [[0, 120], [180, 240]],
                "downtime": [[60, 90]],
                "hourly_cost": 60,
            }
        ],
        "jobs": [
            {
                "id": f"j{index}",
                "job_number": f"JOB-{index}",
                "release_min": 0,
                "due_min": {1: 100, 2: 70, 3: 240}[index],
                "priority": 1,
                "operations": [
                    {
                        "id": f"o{index}",
                        "operation_number": 1,
                        "name": "Cut",
                        "options": [{"machine_id": "m1", "processing_minutes": end - start}],
                    }
                ],
            }
            for index, (start, end) in enumerate(((10, 50), (100, 120), (190, 220)), start=1)
        ],
    }


def _assignments() -> list[dict]:
    return [
        {
            "job_id": f"j{index}",
            "operation_id": f"o{index}",
            "machine_id": "m1",
            "start_min": start,
            "end_min": end,
        }
        for index, (start, end) in enumerate(((10, 50), (100, 120), (190, 220)), start=1)
    ]


def test_utilization_window_clips_busy_calendar_and_downtime() -> None:
    result = compute_utilization_analysis(_snapshot(), _assignments(), 30, 110)
    machine = result["items"][0]
    assert machine["busy_minutes"] == 30
    assert machine["available_minutes"] == 50
    assert machine["idle_minutes"] == 20
    assert machine["downtime_minutes"] == 30
    assert machine["calendar_closed_minutes"] == 0
    assert machine["overtime_minutes"] == 0
    assert machine["scheduled_operation_count"] == 2
    assert machine["due_workload_minutes"] == 40
    assert machine["due_capacity_gap_minutes"] == 0
    assert machine["utilization"] == pytest.approx(0.6)
    assert machine["operating_cost"] == pytest.approx(30)
    assert result["totals"]["utilization"] == pytest.approx(0.6)
    assert result["work_centers"][0]["utilization"] == pytest.approx(0.6)
    assert [(bucket["start_min"], bucket["end_min"]) for bucket in result["series"]] == [
        (30, 90),
        (90, 110),
    ]
    assert result["series"][0]["items"][0]["utilization"] == pytest.approx(20 / 30)
    assert result["series"][1]["items"][0]["utilization"] == pytest.approx(0.5)


def test_utilization_closed_period_has_no_fabricated_denominator() -> None:
    result = compute_utilization_analysis(_snapshot(), _assignments(), 120, 180)
    machine = result["items"][0]
    assert machine["busy_minutes"] == 0
    assert machine["available_minutes"] == 0
    assert machine["unavailable_minutes"] == 60
    assert machine["utilization"] is None
    assert result["metric_reasons"]["utilization"] == "No available machine time in the selected interval"


def test_due_workload_can_exceed_capacity_even_when_busy_time_is_zero() -> None:
    result = compute_utilization_analysis(_snapshot(), _assignments(), 60, 90)
    machine = result["items"][0]
    assert machine["busy_minutes"] == 0
    assert machine["available_minutes"] == 0
    assert machine["due_workload_minutes"] == 20
    assert machine["due_capacity_gap_minutes"] == 20


def test_utilization_rejects_a_window_outside_saved_horizon() -> None:
    with pytest.raises(ValueError, match="within the saved scheduling horizon"):
        compute_utilization_analysis(_snapshot(), _assignments(), 200, 241)
