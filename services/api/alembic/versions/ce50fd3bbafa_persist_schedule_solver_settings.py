"""persist schedule solver settings

Revision ID: ce50fd3bbafa
Revises: 40903fd3a514
Create Date: 2026-11-11 02:36:30.257960
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ce50fd3bbafa"
down_revision: Union[str, None] = "40903fd3a514"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing runs get an empty object: their old solver settings are unknown.
    # A direct ADD COLUMN avoids rebuilding SQLite's FK-referenced run table.
    op.add_column(
        "schedule_runs",
        sa.Column("solver_configuration", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("schedule_runs", "solver_configuration")
