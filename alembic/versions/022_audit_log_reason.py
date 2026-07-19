"""Add reason column to audit_logs for edit-audit-trail

Revision ID: 022
Revises: 021
Create Date: 2026-07-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("reason", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "reason")
