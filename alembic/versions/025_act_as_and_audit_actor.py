"""Add users.acting_as_role and audit_logs.acted_as_role for BM Act-As-Role

Revision ID: 025
Revises: 024
Create Date: 2026-07-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    user_role = postgresql.ENUM(name="user_role", create_type=False)
    op.add_column("users", sa.Column("acting_as_role", user_role, nullable=True))
    op.add_column("audit_logs", sa.Column("acted_as_role", user_role, nullable=True))


def downgrade() -> None:
    op.drop_column("audit_logs", "acted_as_role")
    op.drop_column("users", "acting_as_role")
