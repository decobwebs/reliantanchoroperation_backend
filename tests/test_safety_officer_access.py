"""Safety Officer: sees every operation, changes none of it; keeps the
inspection log; sees the safety dashboard. Required System Update, phase 5.

Runs each route's own role guard against a fake user. No database, no network.

Run:  cd reliant-anchor-api && ./venv/Scripts/python.exe -m pytest tests -q
"""
import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.main import app
from app.models.enums import AuditPhase, DocType, TruckOpStatus, UserRole
from app.routers import documents
from app.schemas.inspection import InspectionActionComplete, InspectionCreate, InspectionUpdate
from app.services.operation_service import OperationService
from app.services.safety_service import missing_audits

SO = UserRole.safety_officer


def _user(role, **kw):
    return SimpleNamespace(id=uuid4(), role=role, acting_as_role=None, **kw)


def _guard_of(route):
    for dep in route.dependant.dependencies:
        if dep.call.__name__ == "_check":
            return dep.call
    return None


def _routes():
    return [r for r in app.routes if isinstance(r, APIRoute)]


def _find(method, path):
    for r in _routes():
        if r.path == "/api/v1" + path and method in r.methods:
            return r
    raise AssertionError(f"no route {method} {path}")


def _passes(method, path, role):
    guard = _guard_of(_find(method, path))
    assert guard, f"no role guard on {method} {path}"
    try:
        asyncio.run(guard(current_user=_user(role)))
        return True
    except HTTPException as exc:
        assert exc.status_code == 403
        return False


# ── Nothing to write but their own logs ────────────────────────────────────────

# The only role-guarded writes the Safety Officer may make: the vessel logs
# (phase 3) and the inspection log (this phase).
SO_WRITE_PREFIXES = (
    "/api/v1/vessels/{vessel_id}/incidents", "/api/v1/vessel-incidents/",
    "/api/v1/vessels/{vessel_id}/maintenance", "/api/v1/vessel-maintenance/",
    "/api/v1/inspections", "/api/v1/inspection-actions/",
    "/api/v1/auth/",
)


def test_safety_officer_writes_only_their_own_logs():
    allowed = []
    for r in _routes():
        if not (r.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            continue
        guard = _guard_of(r)
        if guard is None:
            continue
        try:
            asyncio.run(guard(current_user=_user(SO)))
        except HTTPException:
            continue
        allowed.append(r.path)
    stray = [p for p in allowed if not p.startswith(SO_WRITE_PREFIXES)]
    assert stray == [], stray


def test_safety_officer_cannot_add_documents_to_an_operation():
    with pytest.raises(HTTPException) as exc:
        documents._refuse_watch_only(_user(SO))
    assert exc.value.status_code == 403
    documents._refuse_watch_only(_user(UserRole.cargo_superintendent))


def test_safety_officer_does_not_see_finance_documents():
    assert documents._hides_finance(_user(SO))
    assert not documents._hides_finance(_user(UserRole.bunker_manager))
    assert set(documents.FINANCE_DOC_TYPES) == {DocType.invoice, DocType.payment_voucher, DocType.pfi}


# ── Reads across operations ────────────────────────────────────────────────────

def test_safety_officer_sees_every_operation():
    op = SimpleNamespace(client_id=uuid4(), task_assignments=[])
    OperationService._check_visibility(op, _user(SO))  # no raise
    with pytest.raises(HTTPException):
        OperationService._check_visibility(op, _user(UserRole.logistics_officer))
    with pytest.raises(HTTPException):
        OperationService._check_visibility(op, _user(UserRole.hr))


SO_READS = [
    ("GET", "/operations/{operation_id}/vessel-activities"),
    ("GET", "/vessel-activities/{activity_id}"),
    ("GET", "/vessel-activities/{activity_id}/legs"),
    ("GET", "/vessel-activities/{activity_id}/updates"),
    ("GET", "/vessel-activities/{activity_id}/comments"),
    ("GET", "/operations/{operation_id}/bdns"),
    ("GET", "/bdns/{bdn_id}"),
    ("GET", "/operations/{operation_id}/truck-bdns"),
    ("GET", "/truck-bdns/{truck_bdn_id}"),
    ("GET", "/operations/{operation_id}/vessel-bdns"),
    ("GET", "/operations/{operation_id}/sts-collections"),
    ("GET", "/operations/{operation_id}/terminal-receipts"),
    ("GET", "/operations/{operation_id}/quantity-summary"),
    ("GET", "/operations/{operation_id}/audit-log"),
    ("GET", "/trucks"),
    ("GET", "/trucks/{truck_id}"),
    ("GET", "/trucks/{truck_id}/profile"),
    ("GET", "/trucks/{truck_id}/issues"),
]


@pytest.mark.parametrize("method,path", SO_READS)
def test_safety_officer_reads_operational_records(method, path):
    assert _passes(method, path, SO)
    assert not _passes(method, path, UserRole.hr)


@pytest.mark.parametrize("method,path", [
    ("GET", "/operations/{operation_id}/pfis"),
    ("GET", "/operations/{operation_id}/feedback"),
    ("GET", "/vessels/{vessel_id}/cargo-ledger"),
])
def test_safety_officer_still_kept_from_finance(method, path):
    assert not _passes(method, path, SO)


# ── Inspections and the dashboard ──────────────────────────────────────────────

KEEP = [
    ("GET", "/inspections/owners"),
    ("POST", "/inspections"),
    ("PATCH", "/inspections/{inspection_id}"),
    ("POST", "/inspections/{inspection_id}/actions"),
    ("POST", "/inspections/{inspection_id}/attachments"),
    ("PATCH", "/inspection-actions/{action_id}"),
    ("POST", "/inspection-actions/{action_id}/complete"),
    ("POST", "/inspection-actions/{action_id}/reopen"),
]
VIEW = [
    ("GET", "/inspections"),
    ("GET", "/inspections/{inspection_id}"),
    ("GET", "/inspections/{inspection_id}/attachments/{attachment_id}"),
]
OUTSIDERS = [UserRole.hr, UserRole.finance_manager, UserRole.logistics_officer,
             UserRole.cargo_superintendent, UserRole.marine_operator, UserRole.client]


@pytest.mark.parametrize("method,path", KEEP)
def test_bm_and_safety_officer_keep_the_inspection_log(method, path):
    assert _passes(method, path, UserRole.bunker_manager)
    assert _passes(method, path, SO)
    for role in [UserRole.ops_supervisor, *OUTSIDERS]:
        assert not _passes(method, path, role), role


@pytest.mark.parametrize("method,path", VIEW)
def test_ops_supervisor_reads_inspections(method, path):
    for role in [UserRole.bunker_manager, UserRole.ops_supervisor, SO]:
        assert _passes(method, path, role), role
    for role in OUTSIDERS:
        assert not _passes(method, path, role), role


def test_safety_dashboard_is_bm_and_safety_officer():
    assert _passes("GET", "/safety/overview", UserRole.bunker_manager)
    assert _passes("GET", "/safety/overview", SO)
    for role in [UserRole.ops_supervisor, *OUTSIDERS]:
        assert not _passes("GET", "/safety/overview", role), role


# ── Which truck audits are missing ─────────────────────────────────────────────

def _truck(status=TruckOpStatus.in_transit, arrived=None, discharged=None):
    return SimpleNamespace(status=status, arrived_discharge_at=arrived, discharge_end_at=discharged)


def test_no_audit_due_before_the_truck_reaches_the_jetty():
    assert missing_audits(_truck(), set()) == []


def test_pre_due_at_the_jetty_post_due_after_discharge():
    assert missing_audits(_truck(TruckOpStatus.arrived), set()) == ["pre"]
    assert missing_audits(_truck(TruckOpStatus.discharging), {AuditPhase.pre}) == []
    assert missing_audits(_truck(TruckOpStatus.completed), {AuditPhase.pre}) == ["post"]
    assert missing_audits(_truck(arrived=datetime(2026, 11, 1), discharged=datetime(2026, 11, 1)),
                          {AuditPhase.loading}) == ["pre", "post"]
    assert missing_audits(_truck(TruckOpStatus.completed), {AuditPhase.pre, AuditPhase.post}) == []


# ── What an inspection must carry ──────────────────────────────────────────────

def _inspection(**kw):
    base = dict(subject_type="site", site_name="Apapa jetty", inspected_on=date(2026, 10, 30),
                title="Jetty fire points", result="pass")
    base.update(kw)
    return InspectionCreate(**base)


def test_inspection_names_exactly_its_subject():
    assert _inspection().site_name == "Apapa jetty"
    with pytest.raises(ValidationError):
        _inspection(site_name=None)
    with pytest.raises(ValidationError):
        _inspection(subject_type="vessel")  # no vessel chosen
    with pytest.raises(ValidationError):
        _inspection(vessel_id=uuid4())      # a site inspection naming a vessel too
    with pytest.raises(ValidationError):
        _inspection(subject_type="barge")


def test_findings_needed_unless_it_passed():
    with pytest.raises(ValidationError):
        _inspection(result="fail")
    with pytest.raises(ValidationError):
        _inspection(result="pass_with_findings", findings="  ")
    assert _inspection(result="fail", findings="Two extinguishers expired").result == "fail"


def test_actions_need_a_description_and_finishing_needs_a_note():
    with pytest.raises(ValidationError):
        _inspection(actions=[{"description": " ", "owner_id": str(uuid4()), "due_date": "2026-11-10"}])
    ok = _inspection(actions=[{"description": "Replace extinguishers", "owner_id": str(uuid4()),
                               "due_date": (date(2026, 11, 1) + timedelta(days=7)).isoformat()}])
    assert len(ok.actions) == 1
    with pytest.raises(ValidationError):
        InspectionActionComplete(note="")
    with pytest.raises(ValidationError):
        InspectionUpdate(reason=" ", title="x")
