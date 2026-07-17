"""Standalone invoice support: nullable operation_id, description line item

Revision ID: 017
Revises: 016
Create Date: 2026-07-17
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Allow invoices that aren't tied to an operation (ad-hoc billing) ────
    #    Mirrors 010_standalone_pfi which did the same for pfis.operation_id.
    op.alter_column("invoices", "operation_id", nullable=True)

    # ── 2. Free-text line-item description ────────────────────────────────────
    #    Operation-bound invoices derive the line item from the operation
    #    (type/product/route). A standalone invoice has no operation, so it must
    #    carry its own description for the PDF.
    op.add_column("invoices", sa.Column("description", sa.Text(), nullable=True))

    # NOTE: ix_invoices_operation_id already exists (created with the table), so
    # it is intentionally NOT created here.


def downgrade() -> None:
    op.drop_column("invoices", "description")
    # Only reversible while no standalone invoices exist (operation_id NOT NULL).
    op.alter_column("invoices", "operation_id", nullable=False)
