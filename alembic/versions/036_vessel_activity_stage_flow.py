"""VesselActivity gains the per-vessel-run stage flow (cast off -> discharge
completed), HSE checklist fields, and discharge-arithmetic fields; new
VesselActivityComment append-only log; Document gains an optional
vessel_activity_id scope; doc_type enum gains hse_form/hse_signed_copy.

Revision ID: 036
Revises: 035
Create Date: 2026-07-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New enum type for the stage sequence. One shared instance, reused by
    # every column below with create_type=False — passing separately
    # constructed Enum() objects with the same `name` is unreliable for
    # create_type=False during a fresh op.create_table() (SQLAlchemy issues
    # its own CREATE TYPE regardless unless it's the *same* object).
    vessel_stage = postgresql.ENUM(
        "cast_off", "outbound", "alongside", "hse_check", "discharging", "discharge_completed",
        name="vessel_stage",
    )
    vessel_stage.create(op.get_bind(), checkfirst=True)
    vessel_stage_col = postgresql.ENUM(
        "cast_off", "outbound", "alongside", "hse_check", "discharging", "discharge_completed",
        name="vessel_stage", create_type=False,
    )

    op.add_column("vessel_activities", sa.Column("stage", vessel_stage_col, nullable=True))
    op.add_column("vessel_activities", sa.Column("stage_cast_off_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vessel_activities", sa.Column("stage_outbound_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vessel_activities", sa.Column("stage_alongside_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vessel_activities", sa.Column("stage_hse_check_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vessel_activities", sa.Column("stage_discharging_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vessel_activities", sa.Column("stage_discharge_completed_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("vessel_activities", sa.Column("hse_checklist", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("vessel_activities", sa.Column("hse_result", sa.Enum(name="audit_result", create_type=False), nullable=True))
    op.add_column("vessel_activities", sa.Column("hse_conducted_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("vessel_activities", sa.Column("hse_conducted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vessel_activities", sa.Column("hse_notes", sa.Text(), nullable=True))

    op.add_column("vessel_activities", sa.Column("gov", sa.Numeric(14, 2), nullable=True))
    op.add_column("vessel_activities", sa.Column("vcf", sa.Numeric(8, 4), nullable=True))
    op.add_column("vessel_activities", sa.Column("gsv", sa.Numeric(14, 2), nullable=True))
    op.add_column("vessel_activities", sa.Column("mt_vacuum", sa.Numeric(12, 3), nullable=True))

    op.create_table(
        "vessel_activity_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("vessel_activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vessel_activities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", vessel_stage_col, nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vessel_activity_comments_activity_id", "vessel_activity_comments", ["vessel_activity_id"])

    op.add_column("documents", sa.Column("vessel_activity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vessel_activities.id"), nullable=True))
    op.create_index("ix_documents_vessel_activity_id", "documents", ["vessel_activity_id"])

    # ALTER TYPE ... ADD VALUE cannot run inside a transaction.
    conn = op.get_bind()
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("ALTER TYPE doc_type ADD VALUE IF NOT EXISTS 'hse_form'"))
    conn.execute(sa.text("ALTER TYPE doc_type ADD VALUE IF NOT EXISTS 'hse_signed_copy'"))
    conn.execute(sa.text("BEGIN"))


def downgrade() -> None:
    op.drop_index("ix_documents_vessel_activity_id", table_name="documents")
    op.drop_column("documents", "vessel_activity_id")
    op.drop_index("ix_vessel_activity_comments_activity_id", table_name="vessel_activity_comments")
    op.drop_table("vessel_activity_comments")

    op.drop_column("vessel_activities", "mt_vacuum")
    op.drop_column("vessel_activities", "gsv")
    op.drop_column("vessel_activities", "vcf")
    op.drop_column("vessel_activities", "gov")

    op.drop_column("vessel_activities", "hse_notes")
    op.drop_column("vessel_activities", "hse_conducted_at")
    op.drop_column("vessel_activities", "hse_conducted_by")
    op.drop_column("vessel_activities", "hse_result")
    op.drop_column("vessel_activities", "hse_checklist")

    op.drop_column("vessel_activities", "stage_discharge_completed_at")
    op.drop_column("vessel_activities", "stage_discharging_at")
    op.drop_column("vessel_activities", "stage_hse_check_at")
    op.drop_column("vessel_activities", "stage_alongside_at")
    op.drop_column("vessel_activities", "stage_outbound_at")
    op.drop_column("vessel_activities", "stage_cast_off_at")
    op.drop_column("vessel_activities", "stage")

    op.execute("DROP TYPE vessel_stage")
    # doc_type new enum values are not removed — Postgres doesn't support it.
