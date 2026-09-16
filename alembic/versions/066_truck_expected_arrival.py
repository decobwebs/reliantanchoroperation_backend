"""Planned arrival time on a truck assignment, so on-time delivery can be measured.

The Truck Operations Manager document sets a 95% on-time delivery target,
measured as "Planned vs Actual Arrival Times". RAOMS records the actual
(`arrived_discharge_at`) but has never recorded the plan, so the KPI could not
be computed at all — it was the one metric in the blueprint marked as a gap
rather than a number.

NOTE — this migration differs from 063, 064 and 065. Those only created new
tables and touched nothing existing. This one ALTERs `truck_operations`, a live
table. It is still the safest possible form of that change:

  * the column is nullable with no default and no backfill, so PostgreSQL
    records it as metadata only — no table rewrite, no row locks held while
    data is copied, effectively instant regardless of table size;
  * every existing row keeps working untouched, reading NULL, which the KPI
    engine already treats as "not measured" rather than "missed";
  * nothing existing reads or writes the column, so no current behaviour can
    change.

Revision ID: 066
Revises: 065
Create Date: 2026-08-26
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "truck_operations",
        sa.Column("expected_arrival_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial index: the planned-arrivals screen only ever asks for rows that
    # have a plan set, and those are a small minority.
    op.create_index(
        "ix_truck_operations_expected_arrival",
        "truck_operations",
        ["expected_arrival_at"],
        postgresql_where=sa.text("expected_arrival_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_truck_operations_expected_arrival", table_name="truck_operations")
    op.drop_column("truck_operations", "expected_arrival_at")
