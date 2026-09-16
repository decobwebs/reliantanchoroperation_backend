"""KPI grades — the human judgment part of a score, always with a reason.

Most of what this system measures is countable: litres lost, hours waited,
checks completed. Some of what the job documents actually ask for is not —
"no operational delays due to poor communication", "proactive identification
of high-risk trucks", crew conduct, pilferage vigilance. Pretending those are
measurable would be worse than admitting they are judged.

So they are judged, by a named person, with a written reason, and the person
being graded sees both. Append-only like every other evidence record here: a
changed grade is a new row and the latest one in the period wins, so the
history of what someone was told, and when, survives.

Revision ID: 065
Revises: 064
Create Date: 2026-08-26
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "065"
down_revision: Union[str, None] = "064"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kpi_grades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        # Who is being graded.
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        # 'YYYY-MM', matching kpi_snapshots.
        sa.Column("period", sa.String(7), nullable=False),
        # A catalog metric with source=graded. Kept as a plain string for the
        # same reason kpi_targets does: the catalog grows without a migration.
        sa.Column("metric_key", sa.String(80), nullable=False),
        sa.Column("score", sa.Numeric(6, 2), nullable=False),
        # Required, and enforced at the API layer with a minimum length. A
        # grade without a reason is just an opinion with a number on it.
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("graded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        # Set when a later grade supersedes this one, so the earlier judgment
        # stays readable instead of vanishing.
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    # The read path is "latest grade per person per metric per period".
    op.create_index(
        "ix_kpi_grades_lookup",
        "kpi_grades",
        ["period", "user_id", "metric_key", "created_at"],
    )
    # A grade is on the same 0-100 scale as every computed score, so the
    # rating bands mean the same thing whoever produced the number.
    op.execute("ALTER TABLE kpi_grades ADD CONSTRAINT ck_kpi_grades_score_range "
               "CHECK (score >= 0 AND score <= 100)")


def downgrade() -> None:
    op.execute("ALTER TABLE kpi_grades DROP CONSTRAINT IF EXISTS ck_kpi_grades_score_range")
    op.drop_index("ix_kpi_grades_lookup", table_name="kpi_grades")
    op.drop_table("kpi_grades")
