"""Add address to users — supports the client record's name/address/email
requirement without introducing a separate Client entity.

Revision ID: 034
Revises: 030
Create Date: 2026-07-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "034"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("address", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "address")
