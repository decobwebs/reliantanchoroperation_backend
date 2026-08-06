"""Truck BDN gains receiving_vessel, matching the Vessel BDN.

A Truck BDN records what was delivered but never recorded *who received it* —
the Vessel BDN has carried `receiving_vessel` since it was built. Trucks in one
operation can go to different destinations, so the document has to say which.

Additive and nullable on purpose. The previous migration dropped columns the
deployed code still wrote to and broke live Truck BDN submission; adding a
nullable column cannot do that, so this is safe to apply before the matching
code ships.

Revision ID: 055
Revises: 054
Create Date: 2026-08-06
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "055"
down_revision: Union[str, None] = "054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("truck_bdns", sa.Column("receiving_vessel", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("truck_bdns", "receiving_vessel")
