"""Per-truck received quantity, and one temperature field everywhere.

Two changes, both agreed with the Bunker Manager.

1. Trucks gain the same measurement chain the barge already had. Until now a
   truck recorded one quantity and one temperature; the vessel side captured
   received quantity, density, temperature, VCF, GOV, and derived GSV and MT
   vacuum from those. A Truck BDN built from two bare numbers cannot be
   reconciled against anything, so `truck_operations` gets the same
   `loading_*` block `vessel_activities` carries — per truck, since each truck
   loads separately (the barge loads once).

   Purely additive and nullable: the 61 existing truck_operations rows are
   untouched and keep working off quantity_loaded_mt. They are deliberately
   NOT backfilled — we have no GOV/VCF/density for historical loads, and
   inventing a "received quantity" reading for them would fabricate precision
   that was never measured.

2. temperature_before_loading / temperature_after_loading collapse to a single
   `temperature` on all four tables that carried the pair. Operations record
   one temperature in practice.

   bdns, truck_bdns, vessel_activities and vessel_activity_legs are all empty
   (verified before writing this), so the pair can be dropped outright rather
   than migrated. bdns already had a legacy single `temperature` column, which
   now becomes the one in use.

Revision ID: 054
Revises: 053
Create Date: 2026-08-05
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "054"
down_revision: Union[str, None] = "053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Per-truck loading measurement chain ────────────────────────────
    # Mirrors vessel_activities.loading_* exactly, so both flows read the same.
    op.add_column("truck_operations", sa.Column("loading_received_quantity_litres", sa.Numeric(14, 2), nullable=True))
    op.add_column("truck_operations", sa.Column("loading_density", sa.Numeric(8, 4), nullable=True))
    op.add_column("truck_operations", sa.Column("loading_temperature", sa.Numeric(6, 2), nullable=True))
    op.add_column("truck_operations", sa.Column("loading_vcf", sa.Numeric(8, 4), nullable=True))
    op.add_column("truck_operations", sa.Column("loading_gov", sa.Numeric(14, 2), nullable=True))
    # Derived on write (gsv = gov * vcf, mt_vacuum = gsv * density) but stored,
    # not computed on read — the Bunker Manager can correct either figure, and a
    # generated column would refuse the edit.
    op.add_column("truck_operations", sa.Column("loading_gsv", sa.Numeric(14, 2), nullable=True))
    op.add_column("truck_operations", sa.Column("loading_mt_vacuum", sa.Numeric(12, 3), nullable=True))
    op.add_column("truck_operations", sa.Column("loading_quantity_recorded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("truck_operations", sa.Column("loading_quantity_description", sa.Text(), nullable=True))

    # ── 2. One temperature field ──────────────────────────────────────────
    # bdns already has `temperature`; just drop the pair.
    op.drop_column("bdns", "temperature_before_loading")
    op.drop_column("bdns", "temperature_after_loading")

    op.drop_column("truck_bdns", "temperature_before_loading")
    op.drop_column("truck_bdns", "temperature_after_loading")
    op.add_column("truck_bdns", sa.Column("temperature", sa.Numeric(6, 2), nullable=True))

    op.drop_column("vessel_activities", "loading_temperature_before_loading")
    op.drop_column("vessel_activities", "loading_temperature_after_loading")
    op.add_column("vessel_activities", sa.Column("loading_temperature", sa.Numeric(6, 2), nullable=True))

    op.drop_column("vessel_activity_legs", "temperature_before_loading")
    op.drop_column("vessel_activity_legs", "temperature_after_loading")
    op.add_column("vessel_activity_legs", sa.Column("temperature", sa.Numeric(6, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("vessel_activity_legs", "temperature")
    op.add_column("vessel_activity_legs", sa.Column("temperature_after_loading", sa.Numeric(6, 2), nullable=True))
    op.add_column("vessel_activity_legs", sa.Column("temperature_before_loading", sa.Numeric(6, 2), nullable=True))

    op.drop_column("vessel_activities", "loading_temperature")
    op.add_column("vessel_activities", sa.Column("loading_temperature_after_loading", sa.Numeric(6, 2), nullable=True))
    op.add_column("vessel_activities", sa.Column("loading_temperature_before_loading", sa.Numeric(6, 2), nullable=True))

    op.drop_column("truck_bdns", "temperature")
    op.add_column("truck_bdns", sa.Column("temperature_after_loading", sa.Numeric(6, 2), nullable=True))
    op.add_column("truck_bdns", sa.Column("temperature_before_loading", sa.Numeric(6, 2), nullable=True))

    op.add_column("bdns", sa.Column("temperature_after_loading", sa.Numeric(6, 2), nullable=True))
    op.add_column("bdns", sa.Column("temperature_before_loading", sa.Numeric(6, 2), nullable=True))

    for col in (
        "loading_quantity_description", "loading_quantity_recorded_at",
        "loading_mt_vacuum", "loading_gsv", "loading_gov", "loading_vcf",
        "loading_temperature", "loading_density", "loading_received_quantity_litres",
    ):
        op.drop_column("truck_operations", col)
