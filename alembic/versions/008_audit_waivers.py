"""Add waivers column to truck_safety_audits

Revision ID: 008
Revises: 007
Create Date: 2026-05-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "truck_safety_audits",
        sa.Column("waivers", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb"))
    )


def downgrade() -> None:
    op.drop_column("truck_safety_audits", "waivers")
