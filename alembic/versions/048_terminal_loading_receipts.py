"""Terminal loading receipts.

31 Jul 2026 meeting decision: terminal-sourced loading (VesselSourceType.
terminal) has zero quantity-capture anywhere today. This adds a lean receipt
table — quantity + GOV/GSV/MT only, no density/temperature — feeding the
same Total Loaded Quantity aggregate as truck deliveries. Own table rather
than reusing VesselActivity, for clean separation from the marine/discharge
flow this operation type never touches.

Revision ID: 048
Revises: 047
Create Date: 2026-08-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "048"
down_revision: Union[str, None] = "047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "terminal_loading_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("quantity_litres", sa.Numeric(14, 2), nullable=False),
        sa.Column("gov", sa.Numeric(14, 2), nullable=True),
        sa.Column("gsv", sa.Numeric(14, 2), nullable=True),
        sa.Column("mt_vacuum", sa.Numeric(12, 3), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_terminal_loading_receipts_operation_id", "terminal_loading_receipts", ["operation_id"])


def downgrade() -> None:
    op.drop_index("ix_terminal_loading_receipts_operation_id", table_name="terminal_loading_receipts")
    op.drop_table("terminal_loading_receipts")
