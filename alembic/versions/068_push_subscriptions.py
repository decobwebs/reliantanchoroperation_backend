"""Per-browser web push subscriptions.

A brand-new table, nothing existing is touched — no ALTER, no backfill, no lock
held on a live table — so this is as safe to run during a deploy as 063-065
were.

`endpoint` is UNIQUE, not unique-per-user. A push endpoint identifies a browser
profile, not an app account: two users sharing a field tablet produce the same
endpoint. The subscribe route (app/routers/push.py) upserts on it and
REASSIGNS user_id, so the most recently signed-in user owns the device and the
previous one stops receiving on it the moment the new subscribe lands. A
(user_id, endpoint) composite unique would instead leave two live rows and
deliver one user's operational notifications to another on a shared device --
an information-disclosure bug, not a duplicate-row bug.

Revision ID: 068
Revises: 067
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "068"
down_revision: Union[str, None] = "067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("vapid_key_id", sa.String(16), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_push_subscriptions_user_id_users",
        ),
        sa.UniqueConstraint("endpoint", name="uq_push_subscriptions_endpoint"),
    )
    op.create_index(
        "ix_push_subscriptions_user_id", "push_subscriptions", ["user_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_user_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
