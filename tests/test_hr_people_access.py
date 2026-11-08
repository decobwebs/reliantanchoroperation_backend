"""The HR people pages are for HR and the Bunker Manager only, and only read.

Same method as test_hr_performance_access: run each route's own role guard
against a fake user. No database, no network.

Run:  cd reliant-anchor-api && ./venv/Scripts/python.exe -m pytest tests -q
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.kpi import router_people
from app.models.enums import UserRole

PATHS = [
    "/kpi/people",
    "/kpi/people/{user_id}",
    "/kpi/people/{user_id}/trend",
    "/kpi/people/{user_id}/report",
]


def _guard(path):
    for route in router_people.router.routes:
        if route.path == path and "GET" in route.methods:
            for dep in route.dependant.dependencies:
                if dep.call.__name__ == "_check":
                    return dep.call
    raise AssertionError(f"no role guard on GET {path}")


def _passes(path, role):
    user = SimpleNamespace(role=role, acting_as_role=None)
    try:
        asyncio.run(_guard(path)(current_user=user))
        return True
    except HTTPException as exc:
        assert exc.status_code == 403
        return False


def test_router_only_reads():
    methods = {m for r in router_people.router.routes for m in r.methods}
    assert methods <= {"GET", "HEAD"}


@pytest.mark.parametrize("path", PATHS)
def test_hr_and_bm_may_read(path):
    assert _passes(path, UserRole.hr)
    assert _passes(path, UserRole.bunker_manager)


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("role", [
    UserRole.safety_officer, UserRole.ops_supervisor, UserRole.logistics_officer,
    UserRole.cargo_superintendent, UserRole.finance_manager, UserRole.marine_operator,
    UserRole.client,
])
def test_everyone_else_refused(path, role):
    assert not _passes(path, role)
