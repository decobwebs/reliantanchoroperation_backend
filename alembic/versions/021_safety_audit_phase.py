"""Split truck_safety_audits into Pre/Post phases (one audit per phase per truck_op)

Revision ID: 021
Revises: 020
Create Date: 2026-07-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE audit_phase AS ENUM ('pre', 'post');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sa.text("BEGIN"))

    conn.execute(sa.text(
        "ALTER TABLE truck_safety_audits ADD COLUMN IF NOT EXISTS phase audit_phase NOT NULL DEFAULT 'pre'"
    ))
    conn.execute(sa.text(
        "ALTER TABLE truck_safety_audits ADD COLUMN IF NOT EXISTS header JSONB NOT NULL DEFAULT '{}'::jsonb"
    ))

    # Replace the single-audit-per-truck_op constraint with one per (truck_op, phase)
    conn.execute(sa.text(
        "ALTER TABLE truck_safety_audits DROP CONSTRAINT IF EXISTS truck_safety_audits_truck_op_id_key"
    ))
    conn.execute(sa.text(
        "ALTER TABLE truck_safety_audits ADD CONSTRAINT uq_truck_safety_audits_truck_op_phase UNIQUE (truck_op_id, phase)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE truck_safety_audits DROP CONSTRAINT IF EXISTS uq_truck_safety_audits_truck_op_phase"
    ))
    conn.execute(sa.text(
        "ALTER TABLE truck_safety_audits ADD CONSTRAINT truck_safety_audits_truck_op_id_key UNIQUE (truck_op_id)"
    ))
    conn.execute(sa.text("ALTER TABLE truck_safety_audits DROP COLUMN IF EXISTS header"))
    conn.execute(sa.text("ALTER TABLE truck_safety_audits DROP COLUMN IF EXISTS phase"))
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("DROP TYPE IF EXISTS audit_phase"))
    conn.execute(sa.text("BEGIN"))
