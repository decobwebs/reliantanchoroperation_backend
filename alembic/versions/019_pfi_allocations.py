"""PFI volume drawdown: quantity_litres on PFI + pfi_allocations join table

Revision ID: 019
Revises: 018
Create Date: 2026-07-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pfis", sa.Column("quantity_litres", sa.Numeric(14, 2), nullable=True))

    op.create_table(
        "pfi_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("pfi_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pfis.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quantity_litres", sa.Numeric(14, 2), nullable=False),
        sa.Column("linked_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_pfi_allocations_pfi_id", "pfi_allocations", ["pfi_id"])
    op.create_index("ix_pfi_allocations_operation_id", "pfi_allocations", ["operation_id"])


def downgrade() -> None:
    op.drop_index("ix_pfi_allocations_operation_id", table_name="pfi_allocations")
    op.drop_index("ix_pfi_allocations_pfi_id", table_name="pfi_allocations")
    op.drop_table("pfi_allocations")
    op.drop_column("pfis", "quantity_litres")
