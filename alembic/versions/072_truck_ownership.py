"""Truck ownership — company-owned or contractor.

Management's truck module request (12 Oct 2026) asks for one register of
"all company and contractor trucks". The register existed; nothing said which
was which. One nullable column: existing trucks stay blank (unknown) until
someone sets them, rather than being guessed into either group.

A plain string validated in the schema, not a Postgres enum, so adding a
third kind later needs no ADD VALUE migration (same reasoning as 071).

Revision ID: 072
Revises: 071
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "072"
down_revision: Union[str, None] = "071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trucks", sa.Column("ownership", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("trucks", "ownership")
