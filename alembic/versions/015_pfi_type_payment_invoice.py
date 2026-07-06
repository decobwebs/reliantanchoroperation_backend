"""015 — add pfi_type to pfis, invoice_id to payments

Revision ID: 015
Revises: 014
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade():
    pfi_type_enum = sa.Enum("client_proforma", "supplier_invoice", name="pfi_type")
    pfi_type_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "pfis",
        sa.Column(
            "pfi_type",
            sa.Enum("client_proforma", "supplier_invoice", name="pfi_type"),
            nullable=True,
        ),
    )
    op.execute("UPDATE pfis SET pfi_type = 'client_proforma' WHERE pfi_type IS NULL")
    op.alter_column("pfis", "pfi_type", nullable=False)

    op.add_column(
        "payments",
        sa.Column(
            "invoice_id",
            UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column("payments", "invoice_id")
    op.drop_column("pfis", "pfi_type")
    op.execute("DROP TYPE IF EXISTS pfi_type")
