"""Add operation_active and completion_pending to notification_type enum

Revision ID: 005
Revises: 004
Create Date: 2026-05-01
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # PostgreSQL ALTER TYPE ADD VALUE must run outside a transaction block.
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text(
        "ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'operation_active'"
    ))
    conn.execute(sa.text(
        "ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'completion_pending'"
    ))
    conn.execute(sa.text("BEGIN"))


def downgrade() -> None:
    # PostgreSQL does not support removing enum values.
    # Downgrade is a no-op; remove rows using these values manually if needed.
    pass
