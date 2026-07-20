"""Add waybill_linked_at to truck_operations — timestamp for waiver/plate/driver link

Revision ID: 024
Revises: 023
Create Date: 2026-07-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("truck_operations", sa.Column("waybill_linked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("truck_operations", "waybill_linked_at")
