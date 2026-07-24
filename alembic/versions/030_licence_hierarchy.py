"""Licence hierarchy: PPDL -> BFL -> Naval Clearance, each drawing down
from the level above with balances computed on read (same pattern as
PfiAllocation). Fully additive, independent of any existing table.

Revision ID: 030
Revises: 029
Create Date: 2026-07-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ppdls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ppdl_number", sa.String(30), nullable=False, unique=True),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "uq_ppdl_single_current", "ppdls", ["is_current"],
        unique=True, postgresql_where=sa.text("is_current = true"),
    )

    op.create_table(
        "ppdl_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ppdl_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ppdls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_type", sa.String(50), nullable=False),
        sa.Column("quantity_litres", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ppdl_id", "product_type", name="uq_ppdl_product"),
    )
    op.create_index("ix_ppdl_products_ppdl_id", "ppdl_products", ["ppdl_id"])

    op.create_table(
        "bfls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("bfl_number", sa.String(30), nullable=False, unique=True),
        sa.Column("ppdl_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ppdls.id"), nullable=False),
        sa.Column("product_type", sa.String(50), nullable=False),
        sa.Column("quantity_litres", sa.Numeric(14, 2), nullable=False),
        sa.Column("vessel", sa.String(200), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_bfls_ppdl_id", "bfls", ["ppdl_id"])

    op.create_table(
        "naval_clearances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("clearance_number", sa.String(30), nullable=False, unique=True),
        sa.Column("date_of_loading", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("document_url", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "naval_clearance_drawdowns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("naval_clearance_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("naval_clearances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bfl_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bfls.id"), nullable=False),
        sa.Column("quantity_litres", sa.Numeric(14, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_nc_drawdowns_naval_clearance_id", "naval_clearance_drawdowns", ["naval_clearance_id"])
    op.create_index("ix_nc_drawdowns_bfl_id", "naval_clearance_drawdowns", ["bfl_id"])

    op.create_table(
        "naval_clearance_loading_locations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("naval_clearance_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("naval_clearances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location", sa.String(255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_nc_loading_locations_naval_clearance_id", "naval_clearance_loading_locations", ["naval_clearance_id"])

    op.create_table(
        "naval_clearance_vessels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("naval_clearance_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("naval_clearances.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("vessel_name", sa.String(200), nullable=False),
        sa.Column("imo_number", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_nc_vessels_naval_clearance_id", "naval_clearance_vessels", ["naval_clearance_id"])
    op.create_index("ix_nc_vessels_client_id", "naval_clearance_vessels", ["client_id"])


def downgrade() -> None:
    op.drop_table("naval_clearance_vessels")
    op.drop_table("naval_clearance_loading_locations")
    op.drop_table("naval_clearance_drawdowns")
    op.drop_table("naval_clearances")
    op.drop_table("bfls")
    op.drop_table("ppdl_products")
    op.drop_index("uq_ppdl_single_current", table_name="ppdls")
    op.drop_table("ppdls")
