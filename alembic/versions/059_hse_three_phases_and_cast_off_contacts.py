"""HSE becomes three checks per vessel run, and Cast Off captures client contacts.

Two changes the Bunker Manager asked for on the live run.

1. HSE was a single checklist per vessel activity. It becomes three, each tied
   to a stage (see docs/HSE-CHECKLISTS.md, the BM's own forms, verbatim):

       Pre-Operation    after Alongside          -> the hse_check stage
       During Operation at Commence Discharge
       Post-Operation   at Discharge Completed

   The existing hse_* columns are KEPT AS-IS and now mean the pre check, so
   every checklist already recorded stays exactly where it is and no data has
   to be moved. Two further column sets are added alongside them. This is the
   shape docs/HSE-CHECKLISTS.md recommended for that reason.

   HSE stays non-blocking: it records, it never gates stage movement.

2. Cast Off gains a client block — client name, the client's vessel name, and
   one or more email addresses. Emails are JSONB rather than a comma-joined
   string so the send path never has to re-parse free text, and because these
   recipients get merged with the existing Naval Clearance list, which is
   already a list.

   Storing them on the activity (not a new table) matches their cardinality:
   one client block per vessel run, captured once at Cast Off and editable
   afterwards by the BM.

Revision ID: 059
Revises: 058
Create Date: 2026-08-13
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID

revision: str = "059"
down_revision: Union[str, None] = "058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _audit_result() -> ENUM:
    """The audit_result type already exists — it is what the pre check has used
    all along. create_type=False references it instead of trying to CREATE TYPE
    a second time, which would abort the migration."""
    return ENUM("satisfactory", "not_satisfactory", name="audit_result", create_type=False)


def _hse_columns(phase: str) -> list[sa.Column]:
    return [
        sa.Column(f"hse_{phase}_checklist", JSONB, nullable=False, server_default="[]"),
        sa.Column(f"hse_{phase}_result", _audit_result(), nullable=True),
        sa.Column(f"hse_{phase}_conducted_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column(f"hse_{phase}_conducted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(f"hse_{phase}_notes", sa.Text(), nullable=True),
        sa.Column(f"hse_{phase}_safety_officer", sa.String(200), nullable=True),
    ]


def upgrade() -> None:
    for phase in ("during", "post"):
        for col in _hse_columns(phase):
            op.add_column("vessel_activities", col)

    op.add_column("vessel_activities", sa.Column("cast_off_client_name", sa.String(200), nullable=True))
    op.add_column("vessel_activities", sa.Column("cast_off_client_vessel_name", sa.String(200), nullable=True))
    op.add_column("vessel_activities", sa.Column("cast_off_client_emails", JSONB, nullable=False, server_default="[]"))


def downgrade() -> None:
    for col in ("cast_off_client_emails", "cast_off_client_vessel_name", "cast_off_client_name"):
        op.drop_column("vessel_activities", col)
    for phase in ("post", "during"):
        for suffix in ("safety_officer", "notes", "conducted_at", "conducted_by", "result", "checklist"):
            op.drop_column("vessel_activities", f"hse_{phase}_{suffix}")
