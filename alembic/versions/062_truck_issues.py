"""Truck issue reporting: problems noticed on a truck (mechanical faults,
leaks, document trouble) recorded for record keeping and shown on the truck's
profile. Issues belong to the truck; an operation may be referenced when the
problem arose during one. Open -> resolved, with who reported/resolved and
when.

Revision ID: 062
Revises: 061
Create Date: 2026-08-21
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "062"
down_revision: Union[str, None] = "061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

severity_enum = postgresql.ENUM("low", "medium", "high", name="truck_issue_severity", create_type=False)
status_enum = postgresql.ENUM("open", "resolved", name="truck_issue_status", create_type=False)


def upgrade() -> None:
    severity_enum.create(op.get_bind(), checkfirst=True)
    status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "truck_issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("truck_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trucks.id"), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=True),
        sa.Column("reported_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("severity", severity_enum, nullable=False, server_default="medium"),
        sa.Column("status", status_enum, nullable=False, server_default="open"),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_truck_issues_truck_id", "truck_issues", ["truck_id"])


def downgrade() -> None:
    op.drop_index("ix_truck_issues_truck_id", table_name="truck_issues")
    op.drop_table("truck_issues")
    status_enum.drop(op.get_bind(), checkfirst=True)
    severity_enum.drop(op.get_bind(), checkfirst=True)
