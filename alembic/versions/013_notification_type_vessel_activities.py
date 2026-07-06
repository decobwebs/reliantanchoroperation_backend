"""Add vessel_activity notification_type enum values

Revision ID: 013
Revises: 012
Create Date: 2026-05-06
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction
    conn = op.get_bind()
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text(
        "ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'vessel_activity_assigned'"
    ))
    conn.execute(sa.text(
        "ALTER TYPE notification_type ADD VALUE IF NOT EXISTS 'vessel_activity_completed'"
    ))
    conn.execute(sa.text("BEGIN"))


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; intentional no-op
    pass
