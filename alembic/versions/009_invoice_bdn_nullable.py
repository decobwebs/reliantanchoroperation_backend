"""Make invoices.bdn_id nullable (truck-only operations have no BDN)

Revision ID: 009
Revises: 008
Create Date: 2026-05-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("invoices", "bdn_id", nullable=True)


def downgrade() -> None:
    op.alter_column("invoices", "bdn_id", nullable=False)
