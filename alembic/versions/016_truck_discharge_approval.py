"""016 — truck discharge approval gate + free-text vessel name

Revision ID: 016
Revises: 015
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "truck_operations",
        sa.Column("destination_vessel_name", sa.Text(), nullable=True),
    )
    op.add_column(
        "truck_operations",
        sa.Column("discharge_approved", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "truck_operations",
        sa.Column(
            "discharge_approved_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "truck_operations",
        sa.Column("discharge_approved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("truck_operations", "discharge_approved_at")
    op.drop_column("truck_operations", "discharge_approved_by")
    op.drop_column("truck_operations", "discharge_approved")
    op.drop_column("truck_operations", "destination_vessel_name")
