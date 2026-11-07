"""Inspections and compliance checks, with follow-up actions.

Required System Update (Oct 2026), item 2: the Safety Officer logs
inspections — what was inspected, when, what was found, and the fixes with
an owner and a due date. First version is a simple form, as agreed on
1 Nov 2026; checklist templates come later, once supplied.

Two brand-new tables. Nothing existing is altered, no backfill.

Subject, result and status are plain strings validated in the schemas (same
reasoning as 071 and 074).

Revision ID: 075
Revises: 074
Create Date: 2026-11-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "075"
down_revision: Union[str, None] = "074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inspections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # vessel | truck | operation | site — only the matching reference is set
        sa.Column("subject_type", sa.String(20), nullable=False),
        sa.Column("vessel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vessels.id"), nullable=True),
        sa.Column("truck_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trucks.id"), nullable=True),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=True),
        sa.Column("site_name", sa.String(200), nullable=True),
        sa.Column("inspected_on", sa.Date, nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        # pass | pass_with_findings | fail
        sa.Column("result", sa.String(30), nullable=False),
        sa.Column("findings", sa.Text, nullable=True),
        sa.Column("inspector_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        # [{id, file_name, file_url, mime_type, size_bytes, uploaded_by, uploaded_by_name, uploaded_at}]
        sa.Column("attachments", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_inspections_vessel_id", "inspections", ["vessel_id"])
    op.create_index("ix_inspections_truck_id", "inspections", ["truck_id"])

    op.create_table(
        "inspection_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("inspection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inspections.id"), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("due_date", sa.Date, nullable=False),
        # open | done (done may be reopened)
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("done_note", sa.Text, nullable=True),
        sa.Column("done_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_inspection_actions_inspection_id", "inspection_actions", ["inspection_id"])


def downgrade() -> None:
    op.drop_index("ix_inspection_actions_inspection_id", table_name="inspection_actions")
    op.drop_table("inspection_actions")
    op.drop_index("ix_inspections_truck_id", table_name="inspections")
    op.drop_index("ix_inspections_vessel_id", table_name="inspections")
    op.drop_table("inspections")
