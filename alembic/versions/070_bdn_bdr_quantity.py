"""Add bdns.bdr_quantity_mt — the quantity on the client's BDR.

The BDR (Bunker Delivery Receipt) is the figure the client acknowledges
receiving, independent of our discharge reading and of the receiving
vessel's own received reading. Staff already upload the BDR as a document
(e.g. "MT MOSUNMOLA BDR.pdf"); this records its number so it can be
reported (the operations data set's BDR QTY column). MT(vac), like every
vessel-side figure.

Nullable, no default, no backfill — a plain ADD COLUMN, safe during a deploy.

Revision ID: 070
Revises: 069
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "070"
down_revision: Union[str, None] = "069"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bdns", sa.Column("bdr_quantity_mt", sa.Numeric(12, 3), nullable=True))


def downgrade() -> None:
    op.drop_column("bdns", "bdr_quantity_mt")
