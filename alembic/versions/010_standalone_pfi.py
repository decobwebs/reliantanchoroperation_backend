"""Standalone PFI support: nullable operation_id, extended PFI fields, expanded PFI status enum

Revision ID: 010
Revises: 009
Create Date: 2026-05-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Extend pfi_status enum (non-transactional) ─────────────────────────
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("ALTER TYPE pfi_status ADD VALUE IF NOT EXISTS 'confirmed'"))
    conn.execute(sa.text("ALTER TYPE pfi_status ADD VALUE IF NOT EXISTS 'payment_initiated'"))
    conn.execute(sa.text("ALTER TYPE pfi_status ADD VALUE IF NOT EXISTS 'paid'"))
    conn.execute(sa.text("ALTER TYPE pfi_status ADD VALUE IF NOT EXISTS 'linked'"))
    conn.execute(sa.text("BEGIN"))

    # ── 2. Make pfis.operation_id nullable (standalone PFI support) ───────────
    op.alter_column("pfis", "operation_id", nullable=True)

    # ── 3. Add new PFI fields ─────────────────────────────────────────────────
    op.add_column("pfis", sa.Column("receipt_url", sa.Text, nullable=True))
    op.add_column("pfis", sa.Column("client_ref", sa.String(255), nullable=True))
    op.add_column("pfis", sa.Column(
        "confirmed_by",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ))
    op.add_column("pfis", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True))

    # ── 4. Add pfi_id to operations (PFI-first flow linkage) ──────────────────
    op.add_column("operations", sa.Column(
        "pfi_id",
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("pfis.id", ondelete="SET NULL"),
        nullable=True,
    ))
    op.create_index("ix_operations_pfi_id", "operations", ["pfi_id"])


def downgrade() -> None:
    op.drop_index("ix_operations_pfi_id", table_name="operations")
    op.drop_column("operations", "pfi_id")
    op.drop_column("pfis", "confirmed_at")
    op.drop_column("pfis", "confirmed_by")
    op.drop_column("pfis", "client_ref")
    op.drop_column("pfis", "receipt_url")
    op.alter_column("pfis", "operation_id", nullable=False)
