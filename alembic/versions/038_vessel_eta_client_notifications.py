"""Vessel ETA (append-only history per client-vessel) and client
notification log (one row per recipient per send — structural isolation,
no multi-recipient rows).

Revision ID: 038
Revises: 037
Create Date: 2026-07-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "038"
down_revision: Union[str, None] = "037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vessel_etas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("naval_clearance_vessel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("naval_clearance_vessels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("eta_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("set_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vessel_etas_ncv_id", "vessel_etas", ["naval_clearance_vessel_id"])

    op.create_table(
        "client_notification_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("naval_clearance_vessel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("naval_clearance_vessels.id"), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column("recipient_name", sa.String(150), nullable=False),
        sa.Column("notification_type", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(30), nullable=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("body_snapshot", sa.Text(), nullable=False),
        sa.Column("sent_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("thread_key", sa.String(64), nullable=False),
    )
    op.create_index("ix_client_notification_logs_operation_id", "client_notification_logs", ["operation_id"])
    op.create_index("ix_client_notification_logs_thread_key", "client_notification_logs", ["thread_key"])
    op.create_index("ix_client_notification_logs_client_id", "client_notification_logs", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_client_notification_logs_client_id", table_name="client_notification_logs")
    op.drop_index("ix_client_notification_logs_thread_key", table_name="client_notification_logs")
    op.drop_index("ix_client_notification_logs_operation_id", table_name="client_notification_logs")
    op.drop_table("client_notification_logs")

    op.drop_index("ix_vessel_etas_ncv_id", table_name="vessel_etas")
    op.drop_table("vessel_etas")
