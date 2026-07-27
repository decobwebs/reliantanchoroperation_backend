"""Optional free-text description on Commence Vessel Operation.

Revision ID: 040
Revises: 039
Create Date: 2026-07-27
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "040"
down_revision: Union[str, None] = "039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vessel_activities", sa.Column("commence_description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("vessel_activities", "commence_description")
