"""Allow standalone invoices billed to a manually-entered client (not a registered user)

Revision ID: 018
Revises: 017
Create Date: 2026-07-17
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # client_id is a FK to users, so an ad-hoc client that was never onboarded as
    # a user could not be billed at all. Allow it to be null when the client is
    # captured as free text instead.
    op.alter_column("invoices", "client_id", nullable=True)
    op.add_column("invoices", sa.Column("client_name", sa.String(255), nullable=True))
    op.add_column("invoices", sa.Column("client_email", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "client_email")
    op.drop_column("invoices", "client_name")
    # Only reversible while every invoice has a registered client.
    op.alter_column("invoices", "client_id", nullable=False)
