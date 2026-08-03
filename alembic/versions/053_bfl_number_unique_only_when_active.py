"""Let a deleted BFL's number be reused — live production bug fix.

`bfls.bfl_number` carried a plain UNIQUE constraint while "delete" in the UI
only flipped `is_active = False`. The row therefore stayed in the table and
permanently reserved its number, so a BFL entered by mistake could never be
re-created with the correct details under the same regulatory number.

Replaces the constraint with a partial unique index that only covers live
rows: a number may appear at most once among active BFLs, while any number of
deactivated rows may keep it for history. Paired with a service-layer change
that hard-deletes an undrawn BFL, and with `_ppdl_product_balance` now summing
only active BFLs so a deleted one returns its litres to the PPDL allowance.

Partial indexes are the standard Postgres way to express "unique among the
rows that still count". The index build is on a small table; no data is
touched and no rewrite occurs.

Revision ID: 053
Revises: 052
Create Date: 2026-08-03
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "053"
down_revision: Union[str, None] = "052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_bfls_bfl_number", "bfls", type_="unique")
    op.create_index(
        "uq_bfls_bfl_number_active",
        "bfls",
        ["bfl_number"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    # Reinstating the blanket constraint fails if duplicate numbers exist
    # across active/inactive rows — that is correct: those rows are exactly
    # what the old schema could not represent, and they must be reconciled by
    # hand before rolling back.
    op.drop_index("uq_bfls_bfl_number_active", table_name="bfls")
    op.create_unique_constraint("uq_bfls_bfl_number", "bfls", ["bfl_number"])
