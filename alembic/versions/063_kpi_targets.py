"""KPI targets — Bunker-Manager-editable overrides for the metric catalog.

Append-only by design, following the same convention as ROB entries, vessel
ETAs and sent-email logs: a target is never rewritten in place. Changing a
target inserts a new row with a later `effective_from`, and the reader takes
the newest row that is already in effect. That keeps the history of what a
person was actually being measured against at the time they were measured —
retuning a target must never silently rewrite last month's scores.

Defaults live in code (app/kpi/catalog.py); a row here only ever overrides
one. An empty table is a valid, fully-working state.

Revision ID: 063
Revises: 062
Create Date: 2026-08-25
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "063"
down_revision: Union[str, None] = "062"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kpi_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        # Matches MetricDef.key in app/kpi/catalog.py. Deliberately a plain
        # string and not an enum: the catalog grows every phase, and an enum
        # would mean a migration for each new measure.
        sa.Column("metric_key", sa.String(80), nullable=False),
        # Each is nullable so one row can override just the target, just the
        # weight, or any combination — an unset column falls through to the
        # catalog default rather than zeroing the metric.
        sa.Column("target_value", sa.Numeric(14, 4), nullable=True),
        sa.Column("fail_value", sa.Numeric(14, 4), nullable=True),
        sa.Column("weight", sa.Numeric(6, 4), nullable=True),
        # Lets the BM retire a measure without deleting its history.
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        # Backdating is allowed on purpose: a target agreed mid-month can be
        # made effective from the 1st. The reader compares against this, never
        # against created_at.
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("set_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        # Required at the API layer — same reason-gated convention as every
        # other corrective action in the system.
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    # The read path is always "newest in-effect row for these metric keys",
    # so key + effective_from is the index that matters.
    op.create_index("ix_kpi_targets_key_effective", "kpi_targets", ["metric_key", "effective_from"])


def downgrade() -> None:
    op.drop_index("ix_kpi_targets_key_effective", table_name="kpi_targets")
    op.drop_table("kpi_targets")
