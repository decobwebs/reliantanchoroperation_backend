"""Edit markers on vessel activity updates and comments.

The BM can now correct any recorded detail of an operation. These columns make
a correction visible on the record itself rather than only in the audit log,
so a reader can tell at a glance that what they're looking at was edited,
by whom, and why. Nothing is ever deleted — corrections are edits.

Revision ID: 043
Revises: 042
Create Date: 2026-07-28
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "043"
down_revision: Union[str, None] = "042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("vessel_activity_updates", "vessel_activity_comments")


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(t, sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(t, sa.Column("edited_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True))
        op.add_column(t, sa.Column("edit_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    for t in _TABLES:
        op.drop_column(t, "edit_reason")
        op.drop_column(t, "edited_by")
        op.drop_column(t, "edited_at")
