"""Safety Officer name on HSE checklists.

The paper checklists carry a "Name of Safety Officer" header field. Every
other header field on those forms (bunker tanker, receiving vessel, stage
timings, quantity delivered, discharge date) is already recorded against the
operation and is filled in from there, so this is the only one that needs
storing.

Revision ID: 044
Revises: 043
Create Date: 2026-07-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "044"
down_revision: Union[str, None] = "043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("vessel_activities", "vessel_activity_legs")


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(t, sa.Column("hse_safety_officer", sa.String(200), nullable=True))


def downgrade() -> None:
    for t in _TABLES:
        op.drop_column(t, "hse_safety_officer")
