"""Add initial_rob_mt to vessel_activities for BM-controlled pre-operation ROB

Revision ID: 014
Revises: 013
Create Date: 2026-05-08
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vessel_activities",
        sa.Column("initial_rob_mt", sa.Numeric(12, 3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vessel_activities", "initial_rob_mt")
