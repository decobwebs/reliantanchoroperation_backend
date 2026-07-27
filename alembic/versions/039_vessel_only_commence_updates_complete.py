"""Vessel-only source type (Truck/Terminal label) on operations; VesselActivity
gains the commence/complete timing pairs and the Discharge & Received
Quantity fields for the new vessel-only commence -> updates -> complete ->
quantities flow; new VesselActivityUpdate append-only free-form update log
with optional image. Full Operation's existing stage-based flow is untouched.

Revision ID: 039
Revises: 038
Create Date: 2026-07-25
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "039"
down_revision: Union[str, None] = "038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    vessel_source_type = postgresql.ENUM(
        "truck", "terminal", name="vessel_source_type",
    )
    vessel_source_type.create(op.get_bind(), checkfirst=True)
    vessel_source_type_col = postgresql.ENUM(
        "truck", "terminal", name="vessel_source_type", create_type=False,
    )
    op.add_column("operations", sa.Column("source_type", vessel_source_type_col, nullable=True))

    # ── Vessel-only commence/complete timing pairs — additive, independent
    # of both the old ROB-session flow and the stage flow.
    op.add_column("vessel_activities", sa.Column("commence_system_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vessel_activities", sa.Column("commence_user_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vessel_activities", sa.Column("complete_system_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vessel_activities", sa.Column("complete_user_at", sa.DateTime(timezone=True), nullable=True))

    # ── Discharge & Received Quantity — vessel-only's simpler operational
    # note (litres). gov/vcf/density/gsv/mt_vacuum already exist on this
    # table (built for full_operation's discharge-quantities step) and are
    # reused here, not duplicated.
    op.add_column("vessel_activities", sa.Column("discharged_quantity_litres", sa.Numeric(14, 2), nullable=True))
    op.add_column("vessel_activities", sa.Column("received_quantity_litres", sa.Numeric(14, 2), nullable=True))
    op.add_column("vessel_activities", sa.Column("quantity_recorded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vessel_activities", sa.Column("quantity_description", sa.Text(), nullable=True))

    op.create_table(
        "vessel_activity_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("vessel_activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vessel_activities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vessel_activity_updates_activity_id", "vessel_activity_updates", ["vessel_activity_id"])


def downgrade() -> None:
    op.drop_index("ix_vessel_activity_updates_activity_id", table_name="vessel_activity_updates")
    op.drop_table("vessel_activity_updates")

    op.drop_column("vessel_activities", "quantity_description")
    op.drop_column("vessel_activities", "quantity_recorded_at")
    op.drop_column("vessel_activities", "received_quantity_litres")
    op.drop_column("vessel_activities", "discharged_quantity_litres")

    op.drop_column("vessel_activities", "complete_user_at")
    op.drop_column("vessel_activities", "complete_system_at")
    op.drop_column("vessel_activities", "commence_user_at")
    op.drop_column("vessel_activities", "commence_system_at")

    op.drop_column("operations", "source_type")
    op.execute("DROP TYPE IF EXISTS vessel_source_type")
