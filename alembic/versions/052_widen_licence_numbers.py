"""Widen ppdl_number/bfl_number/clearance_number — live production bug fix.

These are real-world regulatory document numbers (NMDPRA/ALPAS format,
e.g. "NMDPRA/ALPAS/PPDL/2026/403/2300") which routinely exceed the
original VARCHAR(30), causing StringDataRightTruncationError on insert.
Widened to VARCHAR(100), matching the existing free-text convention used
for other externally-issued document numbers (waybill_document_number,
waybill_number). A varchar length increase is a fast, non-blocking
catalog-only change in Postgres — no table rewrite, no data touched.

Revision ID: 052
Revises: 051
Create Date: 2026-08-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "052"
down_revision: Union[str, None] = "051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("ppdls", "ppdl_number", type_=sa.String(100))
    op.alter_column("bfls", "bfl_number", type_=sa.String(100))
    op.alter_column("naval_clearances", "clearance_number", type_=sa.String(100))


def downgrade() -> None:
    op.alter_column("naval_clearances", "clearance_number", type_=sa.String(30))
    op.alter_column("bfls", "bfl_number", type_=sa.String(30))
    op.alter_column("ppdls", "ppdl_number", type_=sa.String(30))
