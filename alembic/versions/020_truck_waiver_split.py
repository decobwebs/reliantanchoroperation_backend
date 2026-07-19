"""Truck waiver/plate identity split: truck_waivers pool, per-operation driver/vendor/waybill fields, trucks_required

Revision ID: 020
Revises: 019
Create Date: 2026-07-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE truck_waiver_status AS ENUM ('available', 'linked');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sa.text("BEGIN"))

    op.create_table(
        "truck_waivers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("waybill_truck_number", sa.String(50), nullable=False, unique=True),
        sa.Column("status", postgresql.ENUM("available", "linked", name="truck_waiver_status", create_type=False), nullable=False, server_default="available"),
        sa.Column("added_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_truck_waivers_status", "truck_waivers", ["status"])

    # Fleet Library: plate-identity profile gains document/chassis fields.
    # Existing truck_number / driver_name / driver_phone columns are left as-is —
    # driver becomes per-assignment going forward (see truck_operations below),
    # but old columns are not dropped to preserve live data.
    op.add_column("trucks", sa.Column("chassis_number", sa.String(100), nullable=True))
    op.add_column("trucks", sa.Column("truck_licence_url", sa.Text, nullable=True))
    op.add_column("trucks", sa.Column("calibration_cert_url", sa.Text, nullable=True))

    # Per-operation assignment: driver/vendor are temporary, tied to this truck_op,
    # not the truck master. waiver_id is populated only once the waybill is generated.
    op.add_column("truck_operations", sa.Column("driver_name", sa.String(150), nullable=True))
    op.add_column("truck_operations", sa.Column("driver_phone", sa.String(20), nullable=True))
    op.add_column("truck_operations", sa.Column("vendor_name", sa.String(200), nullable=True))
    op.add_column("truck_operations", sa.Column("waybill_document_number", sa.String(100), nullable=True))
    op.add_column("truck_operations", sa.Column(
        "waiver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("truck_waivers.id"), nullable=True
    ))
    op.create_index("ix_truck_operations_waiver_id", "truck_operations", ["waiver_id"])

    op.add_column("operations", sa.Column("trucks_required", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("operations", "trucks_required")

    op.drop_index("ix_truck_operations_waiver_id", table_name="truck_operations")
    op.drop_column("truck_operations", "waiver_id")
    op.drop_column("truck_operations", "waybill_document_number")
    op.drop_column("truck_operations", "vendor_name")
    op.drop_column("truck_operations", "driver_phone")
    op.drop_column("truck_operations", "driver_name")

    op.drop_column("trucks", "calibration_cert_url")
    op.drop_column("trucks", "truck_licence_url")
    op.drop_column("trucks", "chassis_number")

    op.drop_index("ix_truck_waivers_status", table_name="truck_waivers")
    op.drop_table("truck_waivers")

    conn = op.get_bind()
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("DROP TYPE IF EXISTS truck_waiver_status"))
    conn.execute(sa.text("BEGIN"))
