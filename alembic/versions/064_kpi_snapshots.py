"""KPI snapshots — frozen monthly scores per person.

Why store these at all, when Phase 1 deliberately computes scorecards on read:
a closed month must stop moving. Once a month has been graded and discussed
with someone, a later correction to an old record should not silently restate
what they scored — that is the same reasoning that makes ROB entries and sent
email logs append-only. The current month is still computed live; only closed
months are written here and marked frozen.

Also creates the `kpi_role_scores_current` view, which is the shape the
Command Center's team-pulse panel already probes for (period, subject_role,
avg_score). Phase 3 degrades gracefully without it, and lights up with it.

Revision ID: 064
Revises: 063
Create Date: 2026-08-25
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "064"
down_revision: Union[str, None] = "063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kpi_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        # 'YYYY-MM'. A plain string, not a date: these are always whole
        # calendar months and the value is used directly as a label.
        sa.Column("period", sa.String(7), nullable=False),
        # 'user' today; 'role' and 'company' rollups reuse the same table.
        sa.Column("subject_type", sa.String(20), nullable=False, server_default="user"),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        # Denormalised so a role rollup never has to re-read users, and so a
        # score keeps the role the person actually held that month.
        sa.Column("subject_role", sa.String(40), nullable=True),
        sa.Column("score", sa.Numeric(6, 2), nullable=True),
        sa.Column("rating", sa.String(30), nullable=True),
        # Per-metric detail: value, score, target and sample size, keyed by
        # metric_key. JSONB so the metric catalog can grow without a migration.
        sa.Column("metrics", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        # False while the month is still running and being recomputed.
        sa.Column("frozen", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    # One row per subject per period. The month-close job upserts on this.
    op.create_index(
        "uq_kpi_snapshots_period_subject",
        "kpi_snapshots",
        ["period", "subject_type", "subject_id"],
        unique=True,
    )
    op.create_index("ix_kpi_snapshots_period_role", "kpi_snapshots", ["period", "subject_role"])

    # Average score per role for the most recent period that has any rows.
    # Named and shaped to match what app/kpi/command_service.py already
    # queries — do not rename without updating that probe.
    op.execute("""
        CREATE VIEW kpi_role_scores_current AS
        SELECT period, subject_role, round(avg(score), 1) AS avg_score
        FROM kpi_snapshots
        WHERE subject_type = 'user'
          AND subject_role IS NOT NULL
          AND score IS NOT NULL
          AND period = (SELECT max(period) FROM kpi_snapshots WHERE subject_type = 'user')
        GROUP BY period, subject_role
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS kpi_role_scores_current")
    op.drop_index("ix_kpi_snapshots_period_role", table_name="kpi_snapshots")
    op.drop_index("uq_kpi_snapshots_period_subject", table_name="kpi_snapshots")
    op.drop_table("kpi_snapshots")
