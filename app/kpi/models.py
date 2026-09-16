import uuid
from datetime import datetime

from sqlalchemy import Column, String, Boolean, DateTime, Text, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class KpiTarget(Base):
    """A Bunker-Manager override of one catalog default.

    Append-only, like every other evidence record in this system: retuning a
    target inserts a new row rather than editing the old one, so the history
    of what someone was actually measured against survives. `effective_from`
    (not created_at) decides which row applies, which allows a target agreed
    mid-month to be backdated to the 1st.

    Each of target_value / fail_value / weight is independently nullable — a
    row may override only the weight and leave the target falling through to
    the catalog.
    """
    __tablename__ = "kpi_targets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    metric_key = Column(String(80), nullable=False)
    target_value = Column(Numeric(14, 4), nullable=True)
    fail_value = Column(Numeric(14, 4), nullable=True)
    weight = Column(Numeric(6, 4), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    effective_from = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    set_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    setter = relationship("User", foreign_keys=[set_by])
