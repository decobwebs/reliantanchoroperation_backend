"""Add truck_safety_audits table

Revision ID: 006
Revises: 005
Create Date: 2026-05-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Create enum if it doesn't already exist (idempotent via DO block)
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE audit_result AS ENUM ('satisfactory', 'not_satisfactory');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """))
    conn.execute(sa.text("BEGIN"))

    # Use raw SQL to avoid SQLAlchemy re-attempting enum creation
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS truck_safety_audits (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            truck_op_id UUID NOT NULL UNIQUE REFERENCES truck_operations(id) ON DELETE CASCADE,
            operation_id UUID NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
            truck_id    UUID NOT NULL REFERENCES trucks(id),
            conducted_by UUID NOT NULL REFERENCES users(id),
            conductor_name VARCHAR(150),
            conducted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            result      audit_result NOT NULL,
            checklist   JSONB NOT NULL DEFAULT '[]'::jsonb,
            notes       TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))

    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_truck_safety_audits_truck_op_id ON truck_safety_audits(truck_op_id)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_truck_safety_audits_operation_id ON truck_safety_audits(operation_id)"
    ))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_truck_safety_audits_operation_id"))
    conn.execute(sa.text("DROP INDEX IF EXISTS ix_truck_safety_audits_truck_op_id"))
    conn.execute(sa.text("DROP TABLE IF EXISTS truck_safety_audits"))
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("DROP TYPE IF EXISTS audit_result"))
    conn.execute(sa.text("BEGIN"))
