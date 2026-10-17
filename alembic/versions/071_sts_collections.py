"""Ship-to-ship collections — the barge collecting product from another vessel.

Management's "hybrid" operation: on one job the barge is filled by trucks AND
by one or more ship-to-ship collections, and the combined total is what it
then delivers to the client. Each collection is a trip of its own (Cast Off,
Alongside, Receiving Started, Receiving Completed), with the ship-to-ship HSE
checks and its own receipt BDN, which the BM or Ops Supervisor approves.
Approval is what credits the barge's ROB.

A brand-new table — nothing existing is altered, no backfill, no lock on a
live table beyond the brief one Postgres takes to add the foreign keys.

Status and stage are plain strings (validated in the service) rather than new
Postgres enums, so a later addition never needs its own ADD VALUE migration.

Revision ID: 071
Revises: 070
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "071"
down_revision: Union[str, None] = "070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sts_collections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("vessel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vessels.id"), nullable=False),
        sa.Column("source_vessel_name", sa.String(200), nullable=False),
        sa.Column("source_vessel_imo", sa.String(20), nullable=True),
        sa.Column("product_type", sa.String(50), nullable=True),
        sa.Column("location", sa.Text, nullable=True),
        # in_progress -> pending -> approved | rejected (-> pending again);
        # cancelled from any of them.
        sa.Column("status", sa.String(20), nullable=False, server_default="in_progress"),
        # cast_off -> alongside -> receiving_started -> receiving_completed
        sa.Column("stage", sa.String(30), nullable=True),
        sa.Column("cast_off_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alongside_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receiving_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receiving_completed_at", sa.DateTime(timezone=True), nullable=True),
        # {"pre"|"during"|"post": {checklist, result, notes, safety_officer,
        #  conducted_by, conducted_by_name, conducted_at}}
        sa.Column("hse", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        # The receipt BDN — what the barge took on, MT(vac) the figure of record.
        sa.Column("receipt_number", sa.String(20), nullable=True, unique=True),
        sa.Column("gov", sa.Numeric(14, 2), nullable=True),
        sa.Column("gsv", sa.Numeric(14, 2), nullable=True),
        sa.Column("mt_vacuum", sa.Numeric(12, 3), nullable=True),
        sa.Column("density", sa.Numeric(8, 4), nullable=True),
        sa.Column("vcf", sa.Numeric(8, 4), nullable=True),
        sa.Column("temperature", sa.Numeric(6, 2), nullable=True),
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_sts_collections_operation_id", "sts_collections", ["operation_id"])


def downgrade() -> None:
    op.drop_index("ix_sts_collections_operation_id", table_name="sts_collections")
    op.drop_table("sts_collections")
