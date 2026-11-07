"""Vessel incident log and maintenance log.

Required System Update (Oct 2026), item 1: incident reporting and vessel
maintenance on the vessel dashboard. Until now a vessel could only be
labelled "Maintenance" — nothing recorded what was done, when, or when the
next service is due — and there was no incident record at all.

Two brand-new tables. Nothing existing is altered, no backfill.

Type, severity and status are plain strings validated in the service (same
reasoning as 071): adding an incident type later needs no ADD VALUE
migration. Attachments are a JSONB list on the row because `documents`
requires an operation, and most incidents and services have none.

Revision ID: 074
Revises: 073
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "074"
down_revision: Union[str, None] = "073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vessel_incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vessel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vessels.id"), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=True),
        sa.Column("vessel_activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vessel_activities.id"), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", sa.Text, nullable=True),
        sa.Column("incident_type", sa.String(30), nullable=False),
        # low | medium | high | critical
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("immediate_action", sa.Text, nullable=True),
        # open -> under_investigation -> closed (closed may be reopened)
        sa.Column("status", sa.String(30), nullable=False, server_default="open"),
        sa.Column("root_cause", sa.Text, nullable=True),
        sa.Column("corrective_action", sa.Text, nullable=True),
        sa.Column("reported_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("closed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        # [{id, file_name, file_url, mime_type, size_bytes, uploaded_by, uploaded_by_name, uploaded_at}]
        sa.Column("attachments", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_vessel_incidents_vessel_id", "vessel_incidents", ["vessel_id"])

    op.create_table(
        "vessel_maintenance",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("vessel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vessels.id"), nullable=False),
        # planned_service | breakdown_repair | inspection | dry_dock | certificate_renewal
        sa.Column("maintenance_type", sa.String(30), nullable=False),
        # What was serviced, or the certificate's name. Records with the same
        # type and title form one series; the newest one sets the next due date.
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        # planned | in_progress | done | cancelled
        sa.Column("status", sa.String(20), nullable=False, server_default="planned"),
        sa.Column("planned_date", sa.Date, nullable=True),
        sa.Column("completed_date", sa.Date, nullable=True),
        # For a certificate this is its expiry date.
        sa.Column("next_due_date", sa.Date, nullable=True),
        sa.Column("performed_by", sa.String(200), nullable=True),
        sa.Column("cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("cost_currency", sa.String(3), nullable=True),
        sa.Column("attachments", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_vessel_maintenance_vessel_id", "vessel_maintenance", ["vessel_id"])


def downgrade() -> None:
    op.drop_index("ix_vessel_maintenance_vessel_id", table_name="vessel_maintenance")
    op.drop_table("vessel_maintenance")
    op.drop_index("ix_vessel_incidents_vessel_id", table_name="vessel_incidents")
    op.drop_table("vessel_incidents")
