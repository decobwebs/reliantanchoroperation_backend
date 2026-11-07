"""Add the safety_officer and hr roles.

Required System Update (Oct 2026): a Safety Officer who can see all
operational activity for inspections, compliance checks and incident
reporting, and an HR user who can see the performance dashboard. Neither
role may move an operation through its pipeline (app/permissions.py).

Purely additive `ADD VALUE` — kept as its own migration because Postgres
won't let a freshly-added enum value be referenced (compared, inserted) in
the same transaction it was added in. No code should assign these roles
until this migration is confirmed live.

Revision ID: 073
Revises: 072
Create Date: 2026-11-01
"""
from typing import Sequence, Union
from alembic import op

revision: str = "073"
down_revision: Union[str, None] = "072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'safety_officer'")
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'hr'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE. Same rebuild as 046's
    # downgrade — refuses to run once either role has been used anywhere.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM users WHERE role IN ('safety_officer','hr') OR acting_as_role IN ('safety_officer','hr'))
               OR EXISTS (SELECT 1 FROM audit_logs WHERE acted_as_role IN ('safety_officer','hr')) THEN
                RAISE EXCEPTION 'Cannot downgrade: safety_officer/hr is referenced by a user or audit log row. Reassign/clean up first.';
            END IF;
        END $$;
    """)
    op.execute("ALTER TYPE user_role RENAME TO user_role_old")
    op.execute(
        "CREATE TYPE user_role AS ENUM ("
        "'bunker_manager','ops_supervisor','logistics_officer','cargo_superintendent',"
        "'finance_manager','client','marine_operator')"
    )
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE user_role USING role::text::user_role, "
        "ALTER COLUMN acting_as_role TYPE user_role USING acting_as_role::text::user_role"
    )
    op.execute(
        "ALTER TABLE audit_logs ALTER COLUMN acted_as_role TYPE user_role "
        "USING acted_as_role::text::user_role"
    )
    op.execute("DROP TYPE user_role_old")
