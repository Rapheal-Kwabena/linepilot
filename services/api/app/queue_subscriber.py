"""Private Vercel Queue subscriber generated from pyproject.toml."""

from __future__ import annotations

import asyncio

from vercel.queue import Message, RetryAfter, subscribe

from app.queue_messages import RUN_TOPIC, RunMessage
from app.workers.queue_runs import process_queued_run


@subscribe(topic=RUN_TOPIC, consumer_group="linepilot-engineering", retry_after=30, max_concurrency=1)
async def process_run(message: Message[RunMessage]) -> None:
    # Keep the SDK's event loop free to renew the delivery lease while the
    # CPU-bound solver or SimPy engine runs in a worker thread.
    outcome = await asyncio.to_thread(process_queued_run, message.payload.kind, message.payload.run_id)
    if outcome == "retry":
        raise RetryAfter(5)
