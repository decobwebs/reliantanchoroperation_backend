"""Regression test for the BDN-000027 failure mode, applied to push.

vessel_bdn_service._send_submitted_emails documents a real incident: BDN-000027
reached the BM as "submitted" while its row was rolled away, because a
notification was sent before the transaction that created it had committed.

push_service.py is built so the same mistake is structurally impossible for
push: notify() only stages a push into the session's `.info` dict, and a
SQLAlchemy `after_commit` / `after_rollback` listener on the sync `Session`
class is what actually releases or drops it. This pins that behaviour against
a plain in-memory SQLite session — no Postgres, no Supabase, no network, so it
runs anywhere in under a second, the same bar test_addable_trucks.py sets.

Run:  cd reliant-anchor-api && ./venv/Scripts/python.exe -m pytest tests -q
"""
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services import push_service


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Pretend VAPID is configured so enqueue() doesn't no-op, and stub the
    drain so this test never touches the network or an event loop — only the
    stage/release/drop bookkeeping is under test here."""
    monkeypatch.setattr(push_service, "_is_configured", lambda: True)
    monkeypatch.setattr(push_service, "_schedule_drain", lambda: None)
    push_service._outbox.clear()
    yield
    push_service._outbox.clear()


@pytest.fixture
def sync_session():
    engine = create_engine("sqlite:///:memory:")
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_push_is_released_on_commit(sync_session):
    user_id = uuid4()
    push_service.enqueue(sync_session, user_id, {"title": "t", "body": "b"})

    # Staged only — nothing in the outbox until commit.
    assert push_service._outbox == []

    sync_session.commit()

    assert len(push_service._outbox) == 1
    assert push_service._outbox[0][0] == user_id


def test_push_is_dropped_on_rollback(sync_session):
    """The BDN-000027 case: whatever happens after enqueue() rolls back, the
    push must never have been released."""
    user_id = uuid4()
    push_service.enqueue(sync_session, user_id, {"title": "t", "body": "b"})

    sync_session.rollback()

    assert push_service._outbox == []


def test_unconfigured_enqueue_is_a_no_op(sync_session, monkeypatch):
    monkeypatch.setattr(push_service, "_is_configured", lambda: False)
    push_service.enqueue(sync_session, uuid4(), {"title": "t", "body": "b"})
    sync_session.commit()
    assert push_service._outbox == []


def test_none_user_id_is_a_no_op(sync_session):
    push_service.enqueue(sync_session, None, {"title": "t", "body": "b"})
    sync_session.commit()
    assert push_service._outbox == []
