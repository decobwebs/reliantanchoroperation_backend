"""Multi-product operations: operation_products table, backfilled from the
existing single product_type/expected_volume_mt columns (which stay in place,
unused going forward, per the additive-only migration policy).

Revision ID: 026
Revises: 025
Create Date: 2026-07-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operation_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("product_type", sa.String(50), nullable=False),
        sa.Column("quantity_mt", sa.Numeric(12, 3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_operation_products_operation_id", "operation_products", ["operation_id"])

    # Backfill: one row per existing operation from its current single
    # product_type/expected_volume_mt (verified: no live operation has a
    # NULL product_type, so this is a complete, lossless backfill).
    op.execute("""
        INSERT INTO operation_products (id, operation_id, product_type, quantity_mt, created_at)
        SELECT gen_random_uuid(), id, product_type, COALESCE(expected_volume_mt, 0), created_at
        FROM operations
        WHERE product_type IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_operation_products_operation_id", table_name="operation_products")
    op.drop_table("operation_products")
