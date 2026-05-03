"""Add waybill fields to truck_operations

Revision ID: 007
Revises: 006
Create Date: 2026-05-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("truck_operations",
                  sa.Column("waybill_number", sa.String(100), nullable=True))
    op.add_column("truck_operations",
                  sa.Column("waybill_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("truck_operations", "waybill_url")
    op.drop_column("truck_operations", "waybill_number")
