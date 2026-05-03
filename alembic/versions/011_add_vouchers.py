"""Add vouchers table for expense/disbursement tracking

Revision ID: 011
Revises: 010
Create Date: 2026-05-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Pre-existing types — do NOT let SQLAlchemy try to CREATE them
_status_enum = postgresql.ENUM(
    "draft", "submitted", "approved", "rejected",
    name="voucher_status",
    create_type=False,
)
_category_enum = postgresql.ENUM(
    "port_fees", "demurrage", "logistics", "bunker_purchase",
    "labour", "agency_fees", "documentation", "customs", "inspection", "other",
    name="vouchercategory",
    create_type=False,
)


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. Create enums (idempotent, outside transaction) ─────────────────────
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text(
        "DO $$ BEGIN "
        "  CREATE TYPE voucher_status AS ENUM ('draft','submitted','approved','rejected'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    ))
    conn.execute(sa.text(
        "DO $$ BEGIN "
        "  CREATE TYPE vouchercategory AS ENUM ("
        "    'port_fees','demurrage','logistics','bunker_purchase',"
        "    'labour','agency_fees','documentation','customs','inspection','other'"
        "  ); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    ))
    conn.execute(sa.text("BEGIN"))

    # ── 2. Create vouchers table ───────────────────────────────────────────────
    op.create_table(
        "vouchers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("voucher_number", sa.String(50), nullable=False, unique=True),
        sa.Column(
            "operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("operations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pfi_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pfis.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "recorded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "approved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("category", _category_enum, nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="NGN"),
        sa.Column("exchange_rate", sa.Numeric(18, 6), nullable=True),
        sa.Column("amount_ngn", sa.Numeric(18, 2), nullable=True),
        sa.Column("supplier_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("receipt_url", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("status", _status_enum, nullable=False, server_default="draft"),
        sa.Column("payment_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("ix_vouchers_operation_id", "vouchers", ["operation_id"])
    op.create_index("ix_vouchers_pfi_id", "vouchers", ["pfi_id"])
    op.create_index("ix_vouchers_status", "vouchers", ["status"])


def downgrade() -> None:
    op.drop_index("ix_vouchers_status", table_name="vouchers")
    op.drop_index("ix_vouchers_pfi_id", table_name="vouchers")
    op.drop_index("ix_vouchers_operation_id", table_name="vouchers")
    op.drop_table("vouchers")
    op.execute("DROP TYPE IF EXISTS voucher_status")
    op.execute("DROP TYPE IF EXISTS vouchercategory")
