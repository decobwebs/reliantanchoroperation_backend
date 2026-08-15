"""Cast Off client emails actually sending: pending_client_notifications
table for the queue -> approve -> send flow, plus loosening two columns on
client_notification_logs that assumed every recipient came from a Naval
Clearance vessel (naval_clearance_vessel_id, client_id) — a Cast Off
recipient is a raw email/name the BM typed in, with no NavalClearanceVessel
or User account behind it.

client_notification_logs keeps meaning exactly what it means today — "this
row is an email that was actually sent." Nothing here is written to that
table until the BM has approved and then sent it.

Revision ID: 061
Revises: 060
Create Date: 2026-08-15
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "061"
down_revision: Union[str, None] = "060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("client_notification_logs", "naval_clearance_vessel_id", nullable=True)
    op.alter_column("client_notification_logs", "client_id", nullable=True)

    op.create_table(
        "pending_client_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("naval_clearance_vessel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("naval_clearance_vessels.id"), nullable=True),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column("recipient_name", sa.String(150), nullable=True),
        sa.Column("notification_type", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(30), nullable=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body_snapshot", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending_approval"),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_log_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("client_notification_logs.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_pending_client_notifications_operation_id", "pending_client_notifications", ["operation_id"])


def downgrade() -> None:
    op.drop_index("ix_pending_client_notifications_operation_id", table_name="pending_client_notifications")
    op.drop_table("pending_client_notifications")
    op.alter_column("client_notification_logs", "client_id", nullable=False)
    op.alter_column("client_notification_logs", "naval_clearance_vessel_id", nullable=False)
