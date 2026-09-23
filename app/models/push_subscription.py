import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class PushSubscription(Base):
    """One row per browser profile that has accepted notifications.

    A user has many of these — phone, tablet, desktop — and each is pushed
    independently.

    `endpoint` is UNIQUE GLOBALLY, deliberately, and not unique per user. A push
    endpoint identifies a BROWSER PROFILE, not an account: when two people share
    a field tablet, both produce the same endpoint. The subscribe route therefore
    upserts on it and REASSIGNS `user_id`, so the newest person to sign in owns
    the device and the previous one stops receiving on it. A (user_id, endpoint)
    composite would instead leave two live rows pointing at one handset and
    deliver one user's operational notifications to another — an information
    disclosure, not a duplicate row.
    """

    __tablename__ = "push_subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    # First 12 characters of the server VAPID public key this subscription was
    # created under. A subscription signed for a key we no longer hold can never
    # be delivered to, so the sender filters on this and prunes rather than
    # retrying forever against a 403.
    vapid_key_id = Column(String(16), nullable=False)
    # Device label for the settings card ("Chrome on Windows"), nothing more.
    user_agent = Column(Text, nullable=True)
    failure_count = Column(Integer, default=0, nullable=False)
    last_error = Column(Text, nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    user = relationship("User", back_populates="push_subscriptions")


Index("ix_push_subscriptions_user_id", PushSubscription.user_id)
