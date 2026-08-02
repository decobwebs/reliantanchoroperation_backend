"""Ad-hoc client contact for a receiving vessel with no registered account.

31 Jul 2026 meeting decision 6: capture only in this build — no send action
wired to this field yet. Settable server-side only once the leg has reached
cast_off.

Revision ID: 050
Revises: 049
Create Date: 2026-08-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "050"
down_revision: Union[str, None] = "049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vessel_activity_legs", sa.Column("adhoc_client_email", sa.String(255), nullable=True))
    op.add_column("vessel_activity_legs", sa.Column("adhoc_client_name", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("vessel_activity_legs", "adhoc_client_name")
    op.drop_column("vessel_activity_legs", "adhoc_client_email")
