"""Vessel-only six-stage journey + multiple receiving vessels.

Loading Commenced/Completed reuse VesselActivity's existing commence/
complete dual-timestamp pair (loading happens once per barge run). New
child table vessel_activity_legs holds one row per receiving vessel, each
running its own Cast Off -> Alongside -> Discharge Commenced -> Discharge
Completed sequence (dual timestamps), own HSE record, own discharge
readings, and own Vessel BDN. New loading_* columns on vessel_activities
hold the one-time Received Quantity + quality readings at loading. BDN
gains vessel_leg_id; vessel_activity_updates gains an optional leg_id tag.

Full Operation is untouched — purely additive, all nullable.

Revision ID: 041
Revises: 040
Create Date: 2026-07-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "041"
down_revision: Union[str, None] = "040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    vessel_leg_stage = postgresql.ENUM(
        "cast_off", "alongside", "discharge_commenced", "discharge_completed",
        name="vessel_leg_stage",
    )
    vessel_leg_stage.create(op.get_bind(), checkfirst=True)
    vessel_leg_stage_col = postgresql.ENUM(
        "cast_off", "alongside", "discharge_commenced", "discharge_completed",
        name="vessel_leg_stage", create_type=False,
    )

    op.create_table(
        "vessel_activity_legs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("vessel_activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vessel_activities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("receiving_vessel_name", sa.String(200), nullable=False),
        sa.Column("imo_number", sa.String(20), nullable=True),
        sa.Column("eta_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),

        sa.Column("stage", vessel_leg_stage_col, nullable=True),
        sa.Column("stage_cast_off_system_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage_cast_off_user_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage_alongside_system_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage_alongside_user_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage_discharge_commenced_system_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage_discharge_commenced_user_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage_discharge_completed_system_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stage_discharge_completed_user_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("hse_checklist", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("hse_result", postgresql.ENUM("satisfactory", "not_satisfactory", name="audit_result", create_type=False), nullable=True),
        sa.Column("hse_conducted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("hse_conducted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hse_notes", sa.Text(), nullable=True),

        sa.Column("quantity_discharged_litres", sa.Numeric(14, 2), nullable=True),
        sa.Column("density", sa.Numeric(8, 4), nullable=True),
        sa.Column("temperature_before_loading", sa.Numeric(6, 2), nullable=True),
        sa.Column("temperature_after_loading", sa.Numeric(6, 2), nullable=True),
        sa.Column("vcf", sa.Numeric(8, 4), nullable=True),
        sa.Column("gov", sa.Numeric(14, 2), nullable=True),
        sa.Column("gsv", sa.Numeric(14, 2), nullable=True),
        sa.Column("mt_vacuum", sa.Numeric(12, 3), nullable=True),
        sa.Column("quantity_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("quantity_description", sa.Text(), nullable=True),

        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_vessel_activity_legs_activity_id", "vessel_activity_legs", ["vessel_activity_id"])

    # ── Loading Received Quantity — one-time intake reading on the parent activity ──
    op.add_column("vessel_activities", sa.Column("loading_received_quantity_litres", sa.Numeric(14, 2), nullable=True))
    op.add_column("vessel_activities", sa.Column("loading_density", sa.Numeric(8, 4), nullable=True))
    op.add_column("vessel_activities", sa.Column("loading_temperature_before_loading", sa.Numeric(6, 2), nullable=True))
    op.add_column("vessel_activities", sa.Column("loading_temperature_after_loading", sa.Numeric(6, 2), nullable=True))
    op.add_column("vessel_activities", sa.Column("loading_vcf", sa.Numeric(8, 4), nullable=True))
    op.add_column("vessel_activities", sa.Column("loading_gov", sa.Numeric(14, 2), nullable=True))
    op.add_column("vessel_activities", sa.Column("loading_gsv", sa.Numeric(14, 2), nullable=True))
    op.add_column("vessel_activities", sa.Column("loading_mt_vacuum", sa.Numeric(12, 3), nullable=True))
    op.add_column("vessel_activities", sa.Column("loading_quantity_recorded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vessel_activities", sa.Column("loading_quantity_description", sa.Text(), nullable=True))

    op.add_column("bdns", sa.Column("vessel_leg_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vessel_activity_legs.id"), nullable=True))
    op.create_index("ix_bdns_vessel_leg_id", "bdns", ["vessel_leg_id"])

    op.add_column("vessel_activity_updates", sa.Column("leg_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vessel_activity_legs.id", ondelete="CASCADE"), nullable=True))
    op.create_index("ix_vessel_activity_updates_leg_id", "vessel_activity_updates", ["leg_id"])


def downgrade() -> None:
    op.drop_index("ix_vessel_activity_updates_leg_id", table_name="vessel_activity_updates")
    op.drop_column("vessel_activity_updates", "leg_id")

    op.drop_index("ix_bdns_vessel_leg_id", table_name="bdns")
    op.drop_column("bdns", "vessel_leg_id")

    op.drop_column("vessel_activities", "loading_quantity_description")
    op.drop_column("vessel_activities", "loading_quantity_recorded_at")
    op.drop_column("vessel_activities", "loading_mt_vacuum")
    op.drop_column("vessel_activities", "loading_gsv")
    op.drop_column("vessel_activities", "loading_gov")
    op.drop_column("vessel_activities", "loading_vcf")
    op.drop_column("vessel_activities", "loading_temperature_after_loading")
    op.drop_column("vessel_activities", "loading_temperature_before_loading")
    op.drop_column("vessel_activities", "loading_density")
    op.drop_column("vessel_activities", "loading_received_quantity_litres")

    op.drop_index("ix_vessel_activity_legs_activity_id", table_name="vessel_activity_legs")
    op.drop_table("vessel_activity_legs")

    op.execute("DROP TYPE IF EXISTS vessel_leg_stage")
