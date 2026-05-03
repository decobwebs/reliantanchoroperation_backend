"""Add loading_location and discharge_location to operations

Revision ID: 004
Revises: 003
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("operations", sa.Column("loading_location", sa.String(255), nullable=True))
    op.add_column("operations", sa.Column("discharge_location", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("operations", "discharge_location")
    op.drop_column("operations", "loading_location")
