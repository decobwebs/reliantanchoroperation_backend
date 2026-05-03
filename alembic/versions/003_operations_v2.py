"""Add product tracking, operation versions, full truck telemetry, vessel discharge events

Revision ID: 003
Revises: 002
Create Date: 2026-04-30
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Extend operation_status enum ───────────────────────────────────────
    # PostgreSQL ADD VALUE is non-transactional — must run outside a transaction block.
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("ALTER TYPE operation_status ADD VALUE IF NOT EXISTS 'active'"))
    conn.execute(sa.text("ALTER TYPE operation_status ADD VALUE IF NOT EXISTS 'pending_completion'"))
    conn.execute(sa.text("BEGIN"))

    # ── 2. Operations: product type, versioning columns ───────────────────────
    op.add_column("operations", sa.Column("product_type", sa.String(50), nullable=True))
    op.add_column("operations", sa.Column("version", sa.Integer, nullable=False, server_default="1"))
    op.add_column("operations", sa.Column(
        "parent_operation_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("operations.id", ondelete="SET NULL"),
        nullable=True,
    ))
    op.add_column("operations", sa.Column("version_notes", sa.Text, nullable=True))
    op.add_column("operations", sa.Column("completion_notes", sa.Text, nullable=True))

    # ── 3. Truck operations: full telemetry ───────────────────────────────────
    op.add_column("truck_operations", sa.Column("product_type", sa.String(50), nullable=True))
    op.add_column("truck_operations", sa.Column("quantity_remaining_mt", sa.Numeric(12, 3), nullable=True))
    op.add_column("truck_operations", sa.Column("spillage_mt", sa.Numeric(12, 3), nullable=True))
    op.add_column("truck_operations", sa.Column("temperature_celsius", sa.Numeric(6, 2), nullable=True))
    op.add_column("truck_operations", sa.Column(
        "supervisor_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ))
    op.add_column("truck_operations", sa.Column("departed_parking_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("truck_operations", sa.Column("arrived_loading_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("truck_operations", sa.Column("departed_loading_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("truck_operations", sa.Column("arrived_discharge_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("truck_operations", sa.Column(
        "events",
        postgresql.JSONB,
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    ))

    # ── 4. ROB entries: link to source truck/vessel, spillage, temperature ────
    op.add_column("rob_entries", sa.Column(
        "truck_operation_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("truck_operations.id", ondelete="SET NULL"),
        nullable=True,
    ))
    op.add_column("rob_entries", sa.Column(
        "source_vessel_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("vessels.id", ondelete="SET NULL"),
        nullable=True,
    ))
    op.add_column("rob_entries", sa.Column("spillage_mt", sa.Numeric(12, 3), nullable=True))
    op.add_column("rob_entries", sa.Column("temperature_celsius", sa.Numeric(6, 2), nullable=True))
    op.add_column("rob_entries", sa.Column(
        "supervisor_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ))

    # ── 5. Vessel discharge events table (vessel → vessel discharge) ──────────
    op.create_table(
        "vessel_discharge_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_vessel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vessels.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "destination_vessel_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vessels.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("product_type", sa.String(50), nullable=True),
        sa.Column("quantity_mt", sa.Numeric(12, 3), nullable=False),
        sa.Column("spillage_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("temperature_celsius", sa.Numeric(6, 2), nullable=True),
        sa.Column("density", sa.Numeric(8, 4), nullable=True),
        sa.Column("discharge_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discharge_end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "supervisor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "rob_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rob_entries.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    op.create_index("ix_vde_operation_id", "vessel_discharge_events", ["operation_id"])
    op.create_index("ix_vde_source_vessel_id", "vessel_discharge_events", ["source_vessel_id"])


def downgrade() -> None:
    op.drop_index("ix_vde_source_vessel_id", "vessel_discharge_events")
    op.drop_index("ix_vde_operation_id", "vessel_discharge_events")
    op.drop_table("vessel_discharge_events")

    for col in ["truck_operation_id", "source_vessel_id", "spillage_mt", "temperature_celsius", "supervisor_id"]:
        op.drop_column("rob_entries", col)

    for col in [
        "product_type", "quantity_remaining_mt", "spillage_mt", "temperature_celsius",
        "supervisor_id", "departed_parking_at", "arrived_loading_at", "departed_loading_at",
        "arrived_discharge_at", "events",
    ]:
        op.drop_column("truck_operations", col)

    for col in ["product_type", "version", "parent_operation_id", "version_notes", "completion_notes"]:
        op.drop_column("operations", col)
    # Note: PostgreSQL does not support DROP VALUE from an enum.
