"""HR reads named performance figures; it changes nothing and sees no money.

Runs each route's own role guard against a fake user. No database, no network.

Run:  cd reliant-anchor-api && ./venv/Scripts/python.exe -m pytest tests -q
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.kpi import router_admin, router_command, router_digest, router_me, router_trends
from app.models.enums import UserRole

ROUTERS = (router_admin.router, router_command.router, router_digest.router,
           router_me.router, router_trends.router)

HR_MAY_READ = [
    ("GET", "/kpi/leaderboard"),
    ("GET", "/kpi/trends/company"),
    ("GET", "/kpi/reports/hr-pack"),
]

HR_REFUSED = [
    ("POST", "/kpi/grades"),
    ("GET", "/kpi/grades/metrics"),
    ("POST", "/kpi/snapshots/close"),
    ("GET", "/kpi/command-center"),
    ("GET", "/kpi/digest/preview"),
    ("POST", "/kpi/digest/send"),
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


@pytest.mark.parametrize("method,path", HR_MAY_READ)
def test_hr_and_bm_may_read(method, path):
    assert _passes(method, path, UserRole.hr)
    assert _passes(method, path, UserRole.bunker_manager)


@pytest.mark.parametrize("method,path", HR_MAY_READ)
@pytest.mark.parametrize("role", [UserRole.safety_officer, UserRole.ops_supervisor,
                                  UserRole.finance_manager, UserRole.client])
def test_others_still_refused(method, path, role):
    assert not _passes(method, path, role)


@pytest.mark.parametrize("method,path", HR_REFUSED)
def test_hr_cannot_grade_close_or_see_command_center(method, path):
    assert not _passes(method, path, UserRole.hr)
    assert _passes(method, path, UserRole.bunker_manager)
