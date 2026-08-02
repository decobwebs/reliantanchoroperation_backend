"""Operation close ROB fields.

31 Jul 2026 meeting decision: closing an operation captures the vessel's
Expected ROB (read off the vessel at close time) against the BM's entered
Actual ROB — shown side by side, never forced to reconcile. Purely additive,
nullable columns; existing completed operations are simply left blank.

Revision ID: 049
Revises: 048
Create Date: 2026-08-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "049"
down_revision: Union[str, None] = "048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("operations", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("operations", sa.Column("expected_rob_mt", sa.Numeric(12, 3), nullable=True))
    op.add_column("operations", sa.Column("actual_rob_mt", sa.Numeric(12, 3), nullable=True))
    op.add_column("operations", sa.Column("rob_closed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True))


def downgrade() -> None:
    op.drop_column("operations", "rob_closed_by")
    op.drop_column("operations", "actual_rob_mt")
    op.drop_column("operations", "expected_rob_mt")
    op.drop_column("operations", "closed_at")
