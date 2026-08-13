"""Rename the two stage timestamp columns to match the stages renamed in 057.

Migration 057 renamed the enum VALUES (outbound -> approach, discharging ->
commence_discharge) but left the timestamp COLUMNS named after the old stages.
That is not cosmetic — both sides address these columns dynamically:

    # vessel_activity_service.advance_stage
    setattr(activity, f"stage_{data.stage.value}_at", data.occurred_at)

    # operations/[id]/page.tsx
    const key = `stage_${s.value}_at`

so after 057 the backend wrote `stage_approach_at`, which is not a mapped
column. setattr does not raise on a declarative model — it just sets a plain
Python attribute that is never persisted, so the stage advanced but its
timestamp was silently dropped, and the UI read the same missing key back as
an em dash. Caught on a live run between logging Cast Off and Approach.

Renaming the columns (rather than teaching both sides a stage -> column map)
keeps the dynamic lookup that the rest of the six-stage flow relies on.

Revision ID: 058
Revises: 057
Create Date: 2026-08-13
"""
from typing import Sequence, Union
from alembic import op

revision: str = "058"
down_revision: Union[str, None] = "057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("vessel_activities", "stage_outbound_at", new_column_name="stage_approach_at")
    op.alter_column("vessel_activities", "stage_discharging_at", new_column_name="stage_commence_discharge_at")


def downgrade() -> None:
    op.alter_column("vessel_activities", "stage_commence_discharge_at", new_column_name="stage_discharging_at")
    op.alter_column("vessel_activities", "stage_approach_at", new_column_name="stage_outbound_at")
