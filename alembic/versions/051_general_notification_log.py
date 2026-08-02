"""General notification log — new BM-controlled internal staff stream.

31 Jul 2026 meeting decision 5: a second, separate notification channel the
BM triggers for operation-status-level updates, picking either all active
staff or specific individuals. Wholly additive — the existing automatic
notifications (finance on finance events, task-assignees on task events)
are completely untouched. Mirrors ClientNotificationLog's one-row-per-
recipient shape.

Revision ID: 051
Revises: 050
Create Date: 2026-08-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "051"
down_revision: Union[str, None] = "050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "operation_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("sent_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_operation_notifications_operation_id", "operation_notifications", ["operation_id"])

    op.create_table(
        "operation_notification_recipients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operation_notification_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operation_notifications.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("notifications.id"), nullable=True),
    )
    op.create_index("ix_operation_notification_recipients_notification_id", "operation_notification_recipients", ["operation_notification_id"])


def downgrade() -> None:
    op.drop_index("ix_operation_notification_recipients_notification_id", table_name="operation_notification_recipients")
    op.drop_table("operation_notification_recipients")
    op.drop_index("ix_operation_notifications_operation_id", table_name="operation_notifications")
    op.drop_table("operation_notifications")
