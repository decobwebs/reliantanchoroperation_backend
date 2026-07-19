"""Add missing 'cancelled'/'completed' values to pfi_status enum

The PfiStatus Python enum (app/models/enums.py) has always declared these
two values and code references them (e.g. confirm_pfi_payment, list_active_pfis),
but no prior migration ever added them to the Postgres enum type — only
migration 010 added confirmed/payment_initiated/paid/linked. This left the DB
enum silently out of sync with the application enum.

Revision ID: 023
Revises: 022
Create Date: 2026-07-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("COMMIT"))
    conn.execute(sa.text("ALTER TYPE pfi_status ADD VALUE IF NOT EXISTS 'cancelled'"))
    conn.execute(sa.text("ALTER TYPE pfi_status ADD VALUE IF NOT EXISTS 'completed'"))
    conn.execute(sa.text("BEGIN"))


def downgrade() -> None:
    # Postgres cannot drop enum values. No-op — matches migration 010's precedent.
    pass
