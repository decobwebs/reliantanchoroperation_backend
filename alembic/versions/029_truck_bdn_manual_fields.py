"""Truck BDN: switch prefilled/locked fields to manual+required, add the
missing paper-form fields (density, temperatures, VCF, GOV, GSV, MTvac), and
add system_* snapshot columns so the Bunker Manager can compare what was
submitted against what the system independently recorded.

truck_bdns has zero live rows at the time of this migration, so existing
columns are safely tightened to NOT NULL in place.

Revision ID: 029
Revises: 028
Create Date: 2026-07-22
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Now manually entered and required — was prefilled/nullable.
    op.alter_column("truck_bdns", "product_type", existing_type=sa.String(100), nullable=False)
    op.alter_column("truck_bdns", "discharge_location", existing_type=sa.Text, nullable=False)
    op.alter_column("truck_bdns", "discharge_commenced_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("truck_bdns", "discharge_completed_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("truck_bdns", "discharge_completion_date", existing_type=sa.Date, nullable=False)

    # Product quality — new, submitter-only.
    op.add_column("truck_bdns", sa.Column("density", sa.Numeric(8, 4), nullable=False))
    op.add_column("truck_bdns", sa.Column("temperature_before_loading", sa.Numeric(6, 2), nullable=False))
    op.add_column("truck_bdns", sa.Column("temperature_after_loading", sa.Numeric(6, 2), nullable=False))

    # Delivery quantity/method — new, submitter-only.
    op.add_column("truck_bdns", sa.Column("vcf", sa.Numeric(8, 4), nullable=False))
    op.add_column("truck_bdns", sa.Column("gov", sa.Numeric(14, 2), nullable=False))
    op.add_column("truck_bdns", sa.Column("gsv", sa.Numeric(14, 2), nullable=False))
    op.add_column("truck_bdns", sa.Column("mt_vacuum", sa.Numeric(12, 3), nullable=False))

    # System-computed snapshot, for BM comparison only.
    op.add_column("truck_bdns", sa.Column("system_product_type", sa.String(100), nullable=True))
    op.add_column("truck_bdns", sa.Column("system_discharge_location", sa.Text, nullable=True))
    op.add_column("truck_bdns", sa.Column("system_quantity_loaded_mt", sa.Numeric(12, 3), nullable=True))
    op.add_column("truck_bdns", sa.Column("system_quantity_discharged_mt", sa.Numeric(12, 3), nullable=True))
    op.add_column("truck_bdns", sa.Column("system_discharge_commenced_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("truck_bdns", sa.Column("system_discharge_completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("truck_bdns", "system_discharge_completed_at")
    op.drop_column("truck_bdns", "system_discharge_commenced_at")
    op.drop_column("truck_bdns", "system_quantity_discharged_mt")
    op.drop_column("truck_bdns", "system_quantity_loaded_mt")
    op.drop_column("truck_bdns", "system_discharge_location")
    op.drop_column("truck_bdns", "system_product_type")

    op.drop_column("truck_bdns", "mt_vacuum")
    op.drop_column("truck_bdns", "gsv")
    op.drop_column("truck_bdns", "gov")
    op.drop_column("truck_bdns", "vcf")

    op.drop_column("truck_bdns", "temperature_after_loading")
    op.drop_column("truck_bdns", "temperature_before_loading")
    op.drop_column("truck_bdns", "density")

    op.alter_column("truck_bdns", "discharge_completion_date", existing_type=sa.Date, nullable=True)
    op.alter_column("truck_bdns", "discharge_completed_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column("truck_bdns", "discharge_commenced_at", existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column("truck_bdns", "discharge_location", existing_type=sa.Text, nullable=True)
    op.alter_column("truck_bdns", "product_type", existing_type=sa.String(100), nullable=True)
