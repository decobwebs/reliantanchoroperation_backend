"""The vendor list, so names stop being retyped by hand.

"3 BROTHERS" and "3 BROTHER" are the same firm. Their record is split across
two lines, so neither shows the truth — and the Truck Operations Manager's job
document requires a vendor performance assessment programme that cannot work
while that keeps happening.

**This works today with no database change.** If a `vendors` table exists it is
used; if not, the list falls back to the distinct names already on
`truck_operations`. So the picker becomes useful immediately, and gets better
if the registry is ever created. Nothing here writes to the database.

Names that normalise to the same value are grouped, and the most-used spelling
is offered as the canonical one — so the next person types nothing and picks
"3 BROTHERS", the split stops widening, and the existing rows are left exactly
as they are.
"""

import logging
from typing import Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.kpi.command_service import _normalise_vendor
from app.models.user import User
from app.schemas.common import StandardResponse

logger = logging.getLogger("raoms")

router = APIRouter(tags=["KPI"])


@router.get("/kpi/vendors", response_model=StandardResponse)
async def list_vendors(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Known vendor names, most-used spelling first.

    Available to any signed-in user: this is a picker feeding a field they can
    already type into freely, so it grants no access they did not have.
    """
    rows: List[Dict] = []

    # Prefer a real registry if one has been created.
    try:
        probe = (await db.execute(text(
            "SELECT to_regclass('public.vendors') AS t"
        ))).mappings().first()
        if probe and probe["t"] is not None:
            registry = (await db.execute(text("""
                SELECT name, is_active FROM vendors
                WHERE is_active ORDER BY name
            """))).mappings().all()
            if registry:
                return StandardResponse.ok(data={
                    "source": "registry",
                    "vendors": [
                        {"name": r["name"], "trips": None, "variants": []}
                        for r in registry
                    ],
                })
    except Exception as exc:
        logger.info("Vendor registry not readable, falling back to usage (%s)", exc)

    # Fall back to what has actually been used, grouped by normalised name.
    try:
        used = (await db.execute(text("""
            SELECT vendor_name AS name, count(*) AS trips
            FROM truck_operations
            WHERE vendor_name IS NOT NULL AND btrim(vendor_name) <> ''
            GROUP BY vendor_name
            ORDER BY count(*) DESC
        """))).mappings().all()
    except Exception as exc:
        logger.warning("Vendor list unavailable: %s", exc)
        return StandardResponse.ok(data={"source": "unavailable", "vendors": []})

    # Group spellings that mean the same firm. The most-used wins, because it
    # is the one people already recognise — not the longest or the newest.
    grouped: Dict[str, Dict] = {}
    for r in used:
        raw = (r["name"] or "").strip()
        if not raw:
            continue
        key = _normalise_vendor(raw)
        entry = grouped.setdefault(key, {"name": raw, "trips": 0, "variants": []})
        entry["trips"] += int(r["trips"] or 0)
        if raw != entry["name"] and raw not in entry["variants"]:
            entry["variants"].append(raw)

    rows = sorted(grouped.values(), key=lambda e: -e["trips"])
    return StandardResponse.ok(data={"source": "usage", "vendors": rows})
