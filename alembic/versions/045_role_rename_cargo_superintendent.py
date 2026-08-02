"""Rename the marine_manager role to cargo_superintendent.

31 Jul 2026 meeting decision: the role on the platform currently called
"Marine Manager" is renamed to "Cargo Superintendent" everywhere it
appears — including the system identifier itself, not just the display
label. `ALTER TYPE ... RENAME VALUE` is a catalog-only, transactional
operation: every existing users.role/acting_as_role row already storing
'marine_manager' reads as 'cargo_superintendent' the instant this commits,
with no separate data migration needed.

Revision ID: 045
Revises: 044
Create Date: 2026-07-31
"""
from typing import Sequence, Union
from alembic import op

revision: str = "045"
down_revision: Union[str, None] = "044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE user_role RENAME VALUE 'marine_manager' TO 'cargo_superintendent'")


def downgrade() -> None:
    op.execute("ALTER TYPE user_role RENAME VALUE 'cargo_superintendent' TO 'marine_manager'")
