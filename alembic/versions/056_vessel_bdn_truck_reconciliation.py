"""Truck-vs-vessel reconciliation on the Vessel BDN (Full Operation only).

The old Start/Receipt/Bunkering/Discharge/Complete ROB-session flow is
retired for full_operation — the Vessel BDN now carries this reconciliation
instead, and approving it is what updates the vessel's ROB.

Revision ID: 056
Revises: 055
Create Date: 2026-08-13
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "056"
down_revision: Union[str, None] = "055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bdns", sa.Column("truck_discharged_total_mt", sa.Numeric(12, 3), nullable=True))
    op.add_column("bdns", sa.Column("vessel_received_total_mt", sa.Numeric(12, 3), nullable=True))
    op.add_column("bdns", sa.Column("truck_variance_mt", sa.Numeric(12, 3), nullable=True))


def downgrade() -> None:
    op.drop_column("bdns", "truck_variance_mt")
    op.drop_column("bdns", "vessel_received_total_mt")
    op.drop_column("bdns", "truck_discharged_total_mt")
