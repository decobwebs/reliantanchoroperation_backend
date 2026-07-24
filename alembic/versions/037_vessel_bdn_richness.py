"""Vessel BDN: extend the existing (thin) bdns table to Truck-BDN richness —
every field manually entered and required at the schema layer, plus a
system_* comparison snapshot captured at submission for BM comparison.
One BDN per vessel run (vessel_activity_id), not just per operation.

Revision ID: 037
Revises: 036
Create Date: 2026-07-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bdns", sa.Column("vessel_activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vessel_activities.id"), nullable=True))
    op.create_index("ix_bdns_vessel_activity_id", "bdns", ["vessel_activity_id"])

    op.add_column("bdns", sa.Column("company_name", sa.String(200), nullable=True))
    op.add_column("bdns", sa.Column("discharge_location", sa.Text(), nullable=True))
    op.add_column("bdns", sa.Column("receiving_vessel", sa.String(200), nullable=True))
    op.add_column("bdns", sa.Column("quantity_loaded_litres", sa.Numeric(14, 2), nullable=True))
    op.add_column("bdns", sa.Column("quantity_discharged_litres", sa.Numeric(14, 2), nullable=True))
    op.add_column("bdns", sa.Column("variance_litres", sa.Numeric(14, 2), nullable=True))
    op.add_column("bdns", sa.Column("temperature_before_loading", sa.Numeric(6, 2), nullable=True))
    op.add_column("bdns", sa.Column("temperature_after_loading", sa.Numeric(6, 2), nullable=True))
    op.add_column("bdns", sa.Column("vcf", sa.Numeric(8, 4), nullable=True))
    op.add_column("bdns", sa.Column("gov", sa.Numeric(14, 2), nullable=True))
    op.add_column("bdns", sa.Column("gsv", sa.Numeric(14, 2), nullable=True))
    op.add_column("bdns", sa.Column("mt_vacuum", sa.Numeric(12, 3), nullable=True))
    op.add_column("bdns", sa.Column("discharge_commenced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bdns", sa.Column("discharge_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bdns", sa.Column("discharge_completion_date", sa.Date(), nullable=True))

    op.add_column("bdns", sa.Column("system_product_type", sa.String(100), nullable=True))
    op.add_column("bdns", sa.Column("system_discharge_location", sa.Text(), nullable=True))
    op.add_column("bdns", sa.Column("system_quantity_loaded_litres", sa.Numeric(14, 2), nullable=True))
    op.add_column("bdns", sa.Column("system_quantity_discharged_litres", sa.Numeric(14, 2), nullable=True))
    op.add_column("bdns", sa.Column("system_discharge_commenced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bdns", sa.Column("system_discharge_completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("bdns", "system_discharge_completed_at")
    op.drop_column("bdns", "system_discharge_commenced_at")
    op.drop_column("bdns", "system_quantity_discharged_litres")
    op.drop_column("bdns", "system_quantity_loaded_litres")
    op.drop_column("bdns", "system_discharge_location")
    op.drop_column("bdns", "system_product_type")

    op.drop_column("bdns", "discharge_completion_date")
    op.drop_column("bdns", "discharge_completed_at")
    op.drop_column("bdns", "discharge_commenced_at")
    op.drop_column("bdns", "mt_vacuum")
    op.drop_column("bdns", "gsv")
    op.drop_column("bdns", "gov")
    op.drop_column("bdns", "vcf")
    op.drop_column("bdns", "temperature_after_loading")
    op.drop_column("bdns", "temperature_before_loading")
    op.drop_column("bdns", "variance_litres")
    op.drop_column("bdns", "quantity_discharged_litres")
    op.drop_column("bdns", "quantity_loaded_litres")
    op.drop_column("bdns", "receiving_vessel")
    op.drop_column("bdns", "discharge_location")
    op.drop_column("bdns", "company_name")

    op.drop_index("ix_bdns_vessel_activity_id", table_name="bdns")
    op.drop_column("bdns", "vessel_activity_id")
