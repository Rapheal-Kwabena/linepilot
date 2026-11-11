"""track queue-backed run claims and retries

Revision ID: e6b91c0a4d27
Revises: ce50fd3bbafa
Create Date: 2026-11-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e6b91c0a4d27"
down_revision: Union[str, None] = "ce50fd3bbafa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("schedule_runs", "simulation_runs"):
        # Direct column additions preserve SQLite's existing FK-referenced rows.
        op.add_column(
            table,
            sa.Column("execution_backend", sa.String(length=20), nullable=False, server_default="local"),
        )
        op.add_column(table, sa.Column("claim_token", sa.String(length=36), nullable=True))
        op.add_column(table, sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(table, sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        op.add_column(table, sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_schedule_execution_status_lease",
        "schedule_runs",
        ["execution_backend", "status", "lease_expires_at"],
    )
    op.create_index(
        "ix_simulation_execution_status_lease",
        "simulation_runs",
        ["execution_backend", "status", "lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_simulation_execution_status_lease", table_name="simulation_runs")
    op.drop_index("ix_schedule_execution_status_lease", table_name="schedule_runs")
    for table in ("simulation_runs", "schedule_runs"):
        op.drop_column(table, "last_enqueued_at")
        op.drop_column(table, "attempt_count")
        op.drop_column(table, "lease_expires_at")
        op.drop_column(table, "claim_token")
        op.drop_column(table, "execution_backend")
