"""Multiple Naval Clearances per operation: operation_naval_clearances join
table, backfilled from the existing single naval_clearance_id column (which
stays in place, unused going forward, per the additive-only migration
policy — same approach as migration 026's operation_products).

The BM needs to attach more than one Naval Clearance to an operation and
remove any one of them independently. Operation.naval_clearance_id can only
ever point at one row, so link_naval_clearance() was silently overwriting it
on a second call instead of adding a second clearance.

Revision ID: 060
Revises: 059
Create Date: 2026-08-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "060"
down_revision: Union[str, None] = "059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operation_naval_clearances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("naval_clearance_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("naval_clearances.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("operation_id", "naval_clearance_id", name="uq_operation_naval_clearance"),
    )
    op.create_index("ix_operation_naval_clearances_operation_id", "operation_naval_clearances", ["operation_id"])
    op.create_index("ix_operation_naval_clearances_naval_clearance_id", "operation_naval_clearances", ["naval_clearance_id"])

    # Backfill: one join row per operation that already has a clearance linked.
    op.execute("""
        INSERT INTO operation_naval_clearances (id, operation_id, naval_clearance_id, created_at)
        SELECT gen_random_uuid(), id, naval_clearance_id, created_at
        FROM operations
        WHERE naval_clearance_id IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_operation_naval_clearances_naval_clearance_id", table_name="operation_naval_clearances")
    op.drop_index("ix_operation_naval_clearances_operation_id", table_name="operation_naval_clearances")
    op.drop_table("operation_naval_clearances")
