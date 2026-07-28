"""Optional free-text description on Loading Completed, mirroring the one
already on Loading Commenced.

Revision ID: 042
Revises: 041
Create Date: 2026-07-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "042"
down_revision: Union[str, None] = "041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vessel_activities", sa.Column("complete_description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("vessel_activities", "complete_description")
