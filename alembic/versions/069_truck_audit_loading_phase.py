"""Add the 'loading' truck safety-audit phase.

Management sent a separate checklist for loading (QHS_RAL_OP_08_TI01 — the
truck inspection before it loads at the depot). The two existing phases are
both jetty checks: Pre (before the truck discharges into the barge) and Post
(close-out). Loading is a third, independent audit on the same truck_op; the
(truck_op_id, phase) unique constraint already allows one row per phase.

Placed BEFORE 'pre' so ORDER BY phase follows the order the work happens in:
loading at the depot, then the jetty Pre and Post checks.

Purely additive `ADD VALUE`, in its own migration because Postgres won't let
a freshly-added enum value be used in the same transaction it was added in.

Revision ID: 069
Revises: 068
Create Date: 2026-10-14
"""
from typing import Sequence, Union
from alembic import op

revision: str = "069"
down_revision: Union[str, None] = "068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE audit_phase ADD VALUE IF NOT EXISTS 'loading' BEFORE 'pre'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE, so the type is rebuilt. Only
    # possible while no audit has been recorded in the loading phase.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM truck_safety_audits WHERE phase = 'loading') THEN
                RAISE EXCEPTION 'Cannot downgrade: loading-phase safety audits exist. Remove them first.';
            END IF;
        END $$;
    """)
    op.execute("ALTER TABLE truck_safety_audits ALTER COLUMN phase DROP DEFAULT")
    op.execute("ALTER TYPE audit_phase RENAME TO audit_phase_old")
    op.execute("CREATE TYPE audit_phase AS ENUM ('pre', 'post')")
    op.execute(
        "ALTER TABLE truck_safety_audits ALTER COLUMN phase TYPE audit_phase "
        "USING phase::text::audit_phase"
    )
    op.execute("ALTER TABLE truck_safety_audits ALTER COLUMN phase SET DEFAULT 'pre'")
    op.execute("DROP TYPE audit_phase_old")
