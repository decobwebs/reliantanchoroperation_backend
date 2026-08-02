"""Split the Vessel BDN's GOV/GSV/MT block into Discharge and Received sides.

31 Jul 2026 meeting decision: the discharging vessel's own readings and the
receiving vessel's independent readings are two separate measurements that
should never be silently merged into one "truth" — keep both, side by side.

Renames the existing gov/gsv/mt_vacuum columns (which were always the
discharging vessel's own readings) to discharge_gov/discharge_gsv/
discharge_mt_vacuum for symmetry, and adds the new received_gov/received_gsv/
received_mt_vacuum columns for the receiving vessel's independent readings.
All three new columns are nullable — optional on submission, since this is
new data existing in-flight operations won't have.

Revision ID: 047
Revises: 046
Create Date: 2026-08-02
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "047"
down_revision: Union[str, None] = "046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("bdns", "gov", new_column_name="discharge_gov")
    op.alter_column("bdns", "gsv", new_column_name="discharge_gsv")
    op.alter_column("bdns", "mt_vacuum", new_column_name="discharge_mt_vacuum")
    op.add_column("bdns", sa.Column("received_gov", sa.Numeric(14, 2), nullable=True))
    op.add_column("bdns", sa.Column("received_gsv", sa.Numeric(14, 2), nullable=True))
    op.add_column("bdns", sa.Column("received_mt_vacuum", sa.Numeric(12, 3), nullable=True))


def downgrade() -> None:
    op.drop_column("bdns", "received_mt_vacuum")
    op.drop_column("bdns", "received_gsv")
    op.drop_column("bdns", "received_gov")
    op.alter_column("bdns", "discharge_mt_vacuum", new_column_name="mt_vacuum")
    op.alter_column("bdns", "discharge_gsv", new_column_name="gsv")
    op.alter_column("bdns", "discharge_gov", new_column_name="gov")
