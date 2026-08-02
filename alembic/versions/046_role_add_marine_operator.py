"""Add the marine_operator role.

31 Jul 2026 meeting decision: a new role for the on-the-ground person
handling cargo and marine work — manages BFL, Naval Clearance, PPDL, and
truck waivers, additively alongside whichever roles already reach those
areas (nothing is taken away from Cargo Superintendent).

Purely additive `ADD VALUE` — kept as its own migration because Postgres
won't let a freshly-added enum value be referenced (compared, inserted) in
the same transaction it was added in. No code should assign this role
until this migration is confirmed live.

Revision ID: 046
Revises: 045
Create Date: 2026-07-31
"""
from typing import Sequence, Union
from alembic import op

revision: str = "046"
down_revision: Union[str, None] = "045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE user_role ADD VALUE 'marine_operator'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE. Downgrading this migration
    # requires rebuilding the enum type without the new value — only do this
    # if no user row has ever been assigned marine_operator; otherwise the
    # rebuild will fail (or silently orphan rows) and needs a manual repair.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM users WHERE role = 'marine_operator' OR acting_as_role = 'marine_operator')
               OR EXISTS (SELECT 1 FROM audit_logs WHERE acted_as_role = 'marine_operator') THEN
                RAISE EXCEPTION 'Cannot downgrade: marine_operator is referenced by a user or audit log row. Reassign/clean up first.';
            END IF;
        END $$;
    """)
    op.execute("ALTER TYPE user_role RENAME TO user_role_old")
    op.execute(
        "CREATE TYPE user_role AS ENUM ("
        "'bunker_manager','ops_supervisor','logistics_officer','cargo_superintendent',"
        "'finance_manager','client')"
    )
    # Every column across the schema that uses this enum — confirmed via
    # information_schema.columns (udt_name='user_role') before writing this:
    # users.role, users.acting_as_role, audit_logs.acted_as_role.
    op.execute(
        "ALTER TABLE users ALTER COLUMN role TYPE user_role USING role::text::user_role, "
        "ALTER COLUMN acting_as_role TYPE user_role USING acting_as_role::text::user_role"
    )
    op.execute(
        "ALTER TABLE audit_logs ALTER COLUMN acted_as_role TYPE user_role "
        "USING acted_as_role::text::user_role"
    )
    op.execute("DROP TYPE user_role_old")
