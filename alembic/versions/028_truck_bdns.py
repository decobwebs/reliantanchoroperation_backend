"""Truck BDN: truck-only equivalent of the vessel BDN — new truck_bdns table
plus a nullable invoices.truck_bdn_id FK. Reuses the existing bdn_status enum.

Revision ID: 028
Revises: 027
Create Date: 2026-07-21
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "truck_bdns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("truck_bdn_number", sa.String(20), nullable=False, unique=True),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("generated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("status", postgresql.ENUM(name="bdn_status", create_type=False), nullable=False, server_default="pending"),
        sa.Column("company_name", sa.String(200), nullable=False),
        sa.Column("product_type", sa.String(100), nullable=True),
        sa.Column("discharge_location", sa.Text, nullable=True),
        sa.Column("quantity_loaded_mt", sa.Numeric(12, 3), nullable=False),
        sa.Column("quantity_discharged_mt", sa.Numeric(12, 3), nullable=False),
        sa.Column("variance_mt", sa.Numeric(12, 3), nullable=True),
        sa.Column("discharge_commenced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discharge_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("discharge_completion_date", sa.Date, nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pdf_url", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_truck_bdns_operation_id", "truck_bdns", ["operation_id"])
    op.create_index("ix_truck_bdns_status", "truck_bdns", ["status"])

    op.add_column("invoices", sa.Column(
        "truck_bdn_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("truck_bdns.id"), nullable=True
    ))
    op.create_index("ix_invoices_truck_bdn_id", "invoices", ["truck_bdn_id"])


def downgrade() -> None:
    op.drop_index("ix_invoices_truck_bdn_id", table_name="invoices")
    op.drop_column("invoices", "truck_bdn_id")

    op.drop_index("ix_truck_bdns_status", table_name="truck_bdns")
    op.drop_index("ix_truck_bdns_operation_id", table_name="truck_bdns")
    op.drop_table("truck_bdns")
