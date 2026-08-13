"""Rename two vessel stages: outbound -> approach, discharging -> commence_discharge.

The Bunker Manager asked for the wording used by the crew, and chose to change
the stored value rather than only the label, so the database and the screen say
the same thing.

ALTER TYPE ... RENAME VALUE is atomic and needs no data migration: enum values
are stored as references, so every existing row follows the rename automatically.
Checked before writing this — no row currently holds either value (one
vessel_activities row is `discharge_completed`, two are NULL), so the blast
radius is nil regardless.

VesselLegStage is a deliberately separate enum (cast_off / alongside /
discharge_commenced / discharge_completed) and is NOT touched here — its comment
in app/models/enums.py is explicit that edits to VesselStage must never affect it.

Revision ID: 057
Revises: 056
Create Date: 2026-08-13
"""
from typing import Sequence, Union
from alembic import op

revision: str = "057"
down_revision: Union[str, None] = "056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE vessel_stage RENAME VALUE 'outbound' TO 'approach'")
    op.execute("ALTER TYPE vessel_stage RENAME VALUE 'discharging' TO 'commence_discharge'")


def downgrade() -> None:
    op.execute("ALTER TYPE vessel_stage RENAME VALUE 'commence_discharge' TO 'discharging'")
    op.execute("ALTER TYPE vessel_stage RENAME VALUE 'approach' TO 'outbound'")
