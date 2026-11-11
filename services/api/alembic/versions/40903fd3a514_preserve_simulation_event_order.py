"""preserve simulation event order

Revision ID: 40903fd3a514
Revises: d1f1d92f0765
Create Date: 2026-11-11 02:18:30.449073
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "40903fd3a514"
down_revision: Union[str, None] = "d1f1d92f0765"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("simulation_events", sa.Column("sequence", sa.Integer(), nullable=True))
    # Older rows did not preserve tie order. Use the previous stable API order
    # for a deterministic backfill; all new runs keep exact emitted order.
    op.execute(
        sa.text(
            "WITH ranked AS ("
            "SELECT id, ROW_NUMBER() OVER (PARTITION BY simulation_run_id "
            "ORDER BY simulation_time, id) - 1 AS position "
            "FROM simulation_events"
            ") "
            "UPDATE simulation_events SET sequence = "
            "(SELECT position FROM ranked WHERE ranked.id = simulation_events.id)"
        )
    )
    with op.batch_alter_table("simulation_events") as batch:
        batch.alter_column("sequence", existing_type=sa.Integer(), nullable=False)
        batch.create_unique_constraint("uq_simulation_event_sequence", ["simulation_run_id", "sequence"])


def downgrade() -> None:
    with op.batch_alter_table("simulation_events") as batch:
        batch.drop_constraint("uq_simulation_event_sequence", type_="unique")
        batch.drop_column("sequence")
