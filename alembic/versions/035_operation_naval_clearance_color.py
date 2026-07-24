"""Operation gains an optional Naval Clearance link (never a gate — attach
now, later, or never) and a colour tag for quick visual identification.

Revision ID: 035
Revises: 034
Create Date: 2026-07-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("operations", sa.Column("naval_clearance_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("naval_clearances.id"), nullable=True))
    op.add_column("operations", sa.Column("color", sa.String(20), nullable=True))
    op.create_index("ix_operations_naval_clearance_id", "operations", ["naval_clearance_id"])


def downgrade() -> None:
    op.drop_index("ix_operations_naval_clearance_id", table_name="operations")
    op.drop_column("operations", "color")
    op.drop_column("operations", "naval_clearance_id")
