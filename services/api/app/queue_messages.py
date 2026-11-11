"""Small typed queue payloads; engineering inputs stay in the database."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from vercel.queue import Topic


class RunMessage(BaseModel):
    kind: Literal["schedule", "simulation"]
    run_id: str


RUN_TOPIC = Topic[RunMessage]("linepilot-engineering-runs")
