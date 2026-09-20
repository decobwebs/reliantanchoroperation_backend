"""The four NMDPRA report columns RAOMS had nowhere to store.

The regulator's sheet has 37 columns per truck load. RAOMS already held most
of them and twelve are the same on every row (kept in system_settings, no
schema needed). These four had no home at all:

  operations.certificate_of_quality     -> column G, e.g. "HPFO R/070325/05"
  operations.certificate_of_completion  -> column T, e.g. "RAL/BDR/24.25"
  bfls.submitted_date                   -> column C
  bfls.approved_date                    -> column D

The two certificates sit on the operation because the sample sheets repeat one
value across every truck row of the same batch. The two dates sit on the BFL
because they describe when that licence was submitted and approved, which is
what column B (REF NO) points at.

All four are nullable with no default and no backfill, so PostgreSQL records
them as metadata only — no table rewrite and no lock held while data copies.
Existing rows read NULL, and the export prints an empty cell for NULL, which
is what the BM fills in by hand today anyway.

Revision ID: 067
Revises: 066
Create Date: 2026-09-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "067"
down_revision: Union[str, None] = "066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("operations", sa.Column("certificate_of_quality", sa.String(200), nullable=True))
    op.add_column("operations", sa.Column("certificate_of_completion", sa.String(200), nullable=True))
    op.add_column("bfls", sa.Column("submitted_date", sa.Date(), nullable=True))
    op.add_column("bfls", sa.Column("approved_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("bfls", "approved_date")
    op.drop_column("bfls", "submitted_date")
    op.drop_column("operations", "certificate_of_completion")
    op.drop_column("operations", "certificate_of_quality")
