"""Make operations.client_id nullable — BM can create an operation before
picking a client, and fill it in later.

Revision ID: 027
Revises: 026
Create Date: 2026-07-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("operations", "client_id", existing_type=postgresql.UUID(as_uuid=True), nullable=True)


def downgrade() -> None:
    op.alter_column("operations", "client_id", existing_type=postgresql.UUID(as_uuid=True), nullable=False)
