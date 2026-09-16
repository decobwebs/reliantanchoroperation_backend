"""The single place the KPI module attaches itself to the API.

Why this file exists: `app/main.py` is the one file outside `app/kpi/` that
the KPI work touches, and it is live production code shared with everything
else. Every phase of this build would otherwise need its own import and
`include_router` line in it, so main.py would keep changing and two parallel
sessions would keep colliding in it.

Instead main.py calls `mount_kpi()` exactly once, inside a guard, and every
phase adds its router *here* — inside the KPI package, which is self-contained
and untracked. main.py should never need editing for KPI work again.

Each router is mounted independently and defensively. A phase that is
half-finished, or has a bad import, must not stop the other phases' endpoints
from working — and must never stop the rest of the API from starting.
"""

import logging
from typing import List, Tuple

logger = logging.getLogger("raoms")


def mount_kpi(app, prefix: str) -> List[str]:
    """Attach every available KPI router to `app`.

    Returns the names that mounted, for logging. Never raises: a KPI module
    that fails to import is a missing feature, not an outage, and this runs
    on a live system where operations staff are working.
    """
    mounted: List[str] = []

    # (label, module path, attribute). Add a line per phase; keep them
    # independent so one failing does not take the others down.
    routers: List[Tuple[str, str, str]] = [
        ("kpi.targets+scorecard", "app.kpi.router", "router"),           # Phase 1
        ("kpi.me+leaderboard", "app.kpi.router_me", "router"),           # Phase 2
        ("kpi.command_center", "app.kpi.router_command", "router"),      # Phase 3
        ("kpi.grading+reports", "app.kpi.router_admin", "router"),       # Phase 4
        ("kpi.client_portal", "app.kpi.router_portal", "router"),        # Phase 5
        ("kpi.planned_arrivals", "app.kpi.router_arrivals", "router"),   # Phase 6
        ("kpi.digest", "app.kpi.router_digest", "router"),               # Phase 8
        ("kpi.vendors", "app.kpi.router_vendors", "router"),             # Phase 9
        ("kpi.trends", "app.kpi.router_trends", "router"),               # Phase 10
    ]

    for label, module_path, attr in routers:
        try:
            module = __import__(module_path, fromlist=[attr])
            app.include_router(getattr(module, attr), prefix=prefix)
            mounted.append(label)
        except ModuleNotFoundError as exc:
            # Expected while a phase is still unbuilt — the module simply is
            # not there yet. Only report it when something *inside* an
            # existing module is missing, not the module itself.
            if getattr(exc, "name", None) == module_path:
                logger.info("KPI: %s not present yet, skipping", label)
            else:
                logger.error("KPI: %s failed to load (%s)", label, exc)
        except Exception as exc:
            logger.error("KPI: %s failed to load (%s)", label, exc)

    return mounted
