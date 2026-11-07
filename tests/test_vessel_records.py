"""Vessel incident and maintenance logs: who may do what, when maintenance
falls due, and what a record must carry.

Runs each route's own role guard against a fake user. No database, no network.

Run:  cd reliant-anchor-api && ./venv/Scripts/python.exe -m pytest tests -q
"""
import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.enums import UserRole
from app.routers import vessel_records, vessels
from app.schemas.vessel_record import (
    VesselIncidentClose, VesselIncidentCreate, VesselIncidentUpdate, VesselMaintenanceCreate,
)
from app.services.vessel_record_service import due_items

ROUTERS = (vessel_records.router, vessels.router)

RECORD_KEEPERS = [UserRole.bunker_manager, UserRole.ops_supervisor,
                  UserRole.cargo_superintendent, UserRole.safety_officer]
OUTSIDERS = [UserRole.hr, UserRole.finance_manager, UserRole.logistics_officer,
             UserRole.marine_operator, UserRole.client]

KEEPER_ROUTES = [
    ("GET", "/vessels/{vessel_id}/incidents"),
    ("POST", "/vessels/{vessel_id}/incidents"),
    ("PATCH", "/vessel-incidents/{incident_id}"),
    ("POST", "/vessel-incidents/{incident_id}/attachments"),
    ("GET", "/vessel-incidents/{incident_id}/attachments/{attachment_id}"),
    ("GET", "/vessels/{vessel_id}/maintenance"),
    ("POST", "/vessels/{vessel_id}/maintenance"),
    ("PATCH", "/vessel-maintenance/{record_id}"),
    ("POST", "/vessel-maintenance/{record_id}/attachments"),
    ("GET", "/vessel-maintenance/{record_id}/attachments/{attachment_id}"),
    ("GET", "/vessels/{vessel_id}/report"),
    ("GET", "/vessel-records/summary"),
]

CLOSER_ROUTES = [
    ("POST", "/vessel-incidents/{incident_id}/investigate"),
    ("POST", "/vessel-incidents/{incident_id}/close"),
    ("POST", "/vessel-incidents/{incident_id}/reopen"),
]


def _guard(method, path):
    for r in ROUTERS:
        for route in r.routes:
            if route.path == path and method in route.methods:
                for dep in route.dependant.dependencies:
                    if dep.call.__name__ == "_check":
                        return dep.call
    raise AssertionError(f"no role guard on {method} {path}")


def _passes(method, path, role):
    user = SimpleNamespace(role=role, acting_as_role=None)
    try:
        asyncio.run(_guard(method, path)(current_user=user))
        return True
    except HTTPException as exc:
        assert exc.status_code == 403
        return False


# ── Access ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", KEEPER_ROUTES)
def test_vessel_staff_and_safety_officer_keep_the_logs(method, path):
    for role in RECORD_KEEPERS:
        assert _passes(method, path, role), role
    for role in OUTSIDERS:
        assert not _passes(method, path, role), role


@pytest.mark.parametrize("method,path", CLOSER_ROUTES)
def test_only_bm_and_safety_officer_close_incidents(method, path):
    assert _passes(method, path, UserRole.bunker_manager)
    assert _passes(method, path, UserRole.safety_officer)
    for role in [UserRole.ops_supervisor, UserRole.cargo_superintendent, *OUTSIDERS]:
        assert not _passes(method, path, role), role


@pytest.mark.parametrize("method,path", [
    ("GET", "/vessels"), ("GET", "/vessels/{vessel_id}"), ("GET", "/vessels/{vessel_id}/bdns"),
])
def test_safety_officer_can_open_the_vessel_page(method, path):
    assert _passes(method, path, UserRole.safety_officer)
    assert not _passes(method, path, UserRole.hr)


def test_cargo_ledger_stays_closed_to_safety_officer():
    # It carries PFI amounts; the Safety Officer does not see finance.
    assert not _passes("GET", "/vessels/{vessel_id}/cargo-ledger", UserRole.safety_officer)


# ── When maintenance falls due ─────────────────────────────────────────────────

TODAY = date(2026, 11, 1)


def _rec(type_="planned_service", title="Main engine service", status="done",
         planned=None, done=None, next_due=None, created=None):
    return SimpleNamespace(
        id=uuid4(), maintenance_type=type_, title=title, status=status,
        planned_date=planned, completed_date=done, next_due_date=next_due,
        created_at=created or datetime(2026, 1, 1),
    )


def test_done_record_is_due_on_its_next_due_date():
    items = due_items([_rec(done=date(2026, 5, 1), next_due=TODAY + timedelta(days=10))], TODAY)
    assert [(i["state"], i["days"]) for i in items] == [("due_soon", 10)]


def test_past_due_is_overdue_and_far_off_is_not_listed():
    assert due_items([_rec(done=date(2026, 1, 1), next_due=TODAY - timedelta(days=3))], TODAY)[0]["state"] == "overdue"
    assert due_items([_rec(done=date(2026, 1, 1), next_due=TODAY + timedelta(days=90))], TODAY) == []


def test_planned_work_is_due_on_its_planned_date():
    items = due_items([_rec(status="planned", planned=TODAY - timedelta(days=1))], TODAY)
    assert items[0]["state"] == "overdue"


def test_newer_record_in_the_series_clears_the_old_reminder():
    old = _rec(done=date(2025, 11, 1), next_due=TODAY - timedelta(days=5))
    # Same series — title compared without case or extra spaces.
    new = _rec(title="  main ENGINE service ", done=TODAY, next_due=TODAY + timedelta(days=180),
               created=datetime(2026, 11, 1))
    assert due_items([old, new], TODAY) == []


def test_different_series_are_kept_apart():
    engine = _rec(done=date(2026, 1, 1), next_due=TODAY - timedelta(days=5))
    cert = _rec(type_="certificate_renewal", title="Class certificate",
                done=date(2025, 12, 1), next_due=TODAY + timedelta(days=20))
    states = sorted(i["state"] for i in due_items([engine, cert], TODAY))
    assert states == ["due_soon", "overdue"]


def test_cancelled_latest_means_nothing_due():
    old = _rec(done=date(2025, 11, 1), next_due=TODAY - timedelta(days=5))
    cancelled = _rec(status="cancelled", planned=TODAY - timedelta(days=1), created=datetime(2026, 10, 1))
    assert due_items([old, cancelled], TODAY) == []


# ── What a record must carry ───────────────────────────────────────────────────

def _incident(**kw):
    base = dict(occurred_at=datetime(2026, 10, 30, 9, 0), incident_type="spill",
                severity="high", title="Hose leak at manifold")
    base.update(kw)
    return VesselIncidentCreate(**base)


def test_incident_accepts_the_agreed_types_and_severities():
    assert _incident(severity="critical").severity == "critical"
    with pytest.raises(ValidationError):
        _incident(incident_type="flood")
    with pytest.raises(ValidationError):
        _incident(severity="severe")
    with pytest.raises(ValidationError):
        _incident(title="   ")


def test_closing_needs_root_cause_and_fix():
    with pytest.raises(ValidationError):
        VesselIncidentClose(root_cause="Worn gasket", corrective_action=" ")
    with pytest.raises(ValidationError):
        VesselIncidentClose(root_cause="", corrective_action="Gasket replaced")


def test_editing_needs_a_reason():
    with pytest.raises(ValidationError):
        VesselIncidentUpdate(reason="  ", severity="low")
    assert VesselIncidentUpdate(reason="Wrong severity", severity="low").severity == "low"


def test_maintenance_done_needs_a_date_and_cost_is_optional():
    with pytest.raises(ValidationError):
        VesselMaintenanceCreate(maintenance_type="dry_dock", title="Dry dock", status="done")
    rec = VesselMaintenanceCreate(maintenance_type="dry_dock", title="Dry dock",
                                  status="done", completed_date=date(2026, 10, 1))
    assert rec.cost is None
    with pytest.raises(ValidationError):
        VesselMaintenanceCreate(maintenance_type="dry_dock", title="Dry dock", cost=-5)
