from typing import List, Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field


class PushKeys(BaseModel):
    """The two keys inside a browser PushSubscription.toJSON().keys."""
    p256dh: str
    auth: str


class PushSubscribeIn(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: PushKeys
    # expirationTime is part of the browser's PushSubscription.toJSON() but is
    # rarely populated and we don't act on it — the push service itself
    # returns 410 Gone once a subscription actually expires.
    expirationTime: Optional[float] = None


class PushUnsubscribeIn(BaseModel):
    endpoint: str = Field(max_length=2000)


class PushRotateIn(BaseModel):
    """Body sent by the service worker's pushsubscriptionchange handler.
    Unauthenticated — see app/routers/push.py for why that's safe here."""
    old_endpoint: Optional[str] = Field(default=None, max_length=2000)
    endpoint: str = Field(max_length=2000)
    keys: PushKeys
    expirationTime: Optional[float] = None


class VapidPublicKeyOut(BaseModel):
    public_key: str
    configured: bool


class PushSubscriptionOut(BaseModel):
    id: UUID
    endpoint: str
    user_agent: Optional[str] = None
    created_at: datetime
    last_success_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PushTestResult(BaseModel):
    sent: int
    failed: int
    devices: int
