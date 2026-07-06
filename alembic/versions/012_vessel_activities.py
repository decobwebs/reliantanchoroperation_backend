"""Add vessel_activities table for Marine Supervisor oversight sessions

Revision ID: 012
Revises: 011
Create Date: 2026-05-05
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_status_enum = postgresql.ENUM(
    "pending", "active", "completed", "cancelled",
    name="vessel_activity_status",
    create_type=False,
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Create enum (idempotent, outside transaction) ──────────────────────
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text(
        "DO $$ BEGIN "
        "  CREATE TYPE vessel_activity_status AS ENUM "
        "    ('pending', 'active', 'completed', 'cancelled'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    ))
    conn.execute(sa.text("BEGIN"))

    # ── 2. Create vessel_activities table ─────────────────────────────────────
    op.create_table(
        "vessel_activities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("activity_number", sa.String(20), nullable=False, unique=True),
        sa.Column(
            "operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "vessel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vessels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assigned_to",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assigned_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        # Quantity tracking
        sa.Column("truck_delivered_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("vessel_received_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("variance_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("previous_rob_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("new_rob_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("quantity_discharged_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("final_rob_mt", sa.Numeric(12, 3), nullable=True),
        # Physical
        sa.Column("product_type", sa.String(50), nullable=True),
        sa.Column("temperature_celsius", sa.Numeric(6, 2), nullable=True),
        sa.Column("density", sa.Numeric(8, 4), nullable=True),
        sa.Column("spillage_mt", sa.Numeric(12, 3), nullable=True),
        # Timing
        sa.Column("bunkering_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bunkering_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discharge_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discharge_end_at", sa.DateTime(timezone=True), nullable=True),
        # Status and meta
        sa.Column("status", _status_enum, nullable=False, server_default="pending"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("completion_notes", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("ix_vessel_activities_operation_id", "vessel_activities", ["operation_id"])
    op.create_index("ix_vessel_activities_vessel_id", "vessel_activities", ["vessel_id"])
    op.create_index("ix_vessel_activities_assigned_to", "vessel_activities", ["assigned_to"])
    op.create_index("ix_vessel_activities_status", "vessel_activities", ["status"])


def downgrade() -> None:
    op.drop_index("ix_vessel_activities_status", table_name="vessel_activities")
    op.drop_index("ix_vessel_activities_assigned_to", table_name="vessel_activities")
    op.drop_index("ix_vessel_activities_vessel_id", table_name="vessel_activities")
    op.drop_index("ix_vessel_activities_operation_id", table_name="vessel_activities")
    op.drop_table("vessel_activities")
    op.execute("DROP TYPE IF EXISTS vessel_activity_status")
