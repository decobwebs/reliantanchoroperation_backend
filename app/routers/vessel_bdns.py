from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.schemas.vessel_bdn import VesselBdnCreate, VesselBdnUpdate, VesselBdnOut, VesselBdnRejectRequest
from app.services.vessel_bdn_service import VesselBdnService

router = APIRouter(tags=["Vessel BDNs"])

_submit_roles = Depends(require_roles(UserRole.ops_supervisor, UserRole.marine_manager))
_review_roles = Depends(require_roles(UserRole.bunker_manager, UserRole.marine_manager, UserRole.ops_supervisor, UserRole.finance_manager))
_bm_only = Depends(require_roles(UserRole.bunker_manager))


@router.post(
    "/vessel-activities/{vessel_activity_id}/bdn",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_vessel_bdn(
    vessel_activity_id: UUID,
    body: VesselBdnCreate,
    current_user: User = _submit_roles,
    db: AsyncSession = Depends(get_db),
):
    """Submit a Vessel BDN for one vessel run. Ops Supervisor or Marine Manager.
    Every field manually entered and required — nothing is prefilled."""
    bdn = await VesselBdnService.create_vessel_bdn(vessel_activity_id, body, current_user, db)
    data = VesselBdnOut.model_validate(bdn).model_dump()
    data["generated_by_name"] = getattr(bdn, "_generated_by_name", None)
    return StandardResponse.ok(
        data=data,
        message=f"Vessel BDN {bdn.bdn_number} submitted — awaiting Bunker Manager approval",
    )


@router.get("/operations/{operation_id}/vessel-bdns", response_model=StandardResponse)
async def list_vessel_bdns(
    operation_id: UUID,
    current_user: User = _review_roles,
    db: AsyncSession = Depends(get_db),
):
    bdns = await VesselBdnService.list_vessel_bdns(operation_id, db)
    items = []
    for b in bdns:
        item = VesselBdnOut.model_validate(b).model_dump()
        item["generated_by_name"] = getattr(b, "_generated_by_name", None)
        items.append(item)
    return StandardResponse.ok(data=items)


@router.put("/vessel-bdns/{bdn_id}", response_model=StandardResponse)
async def update_vessel_bdn(
    bdn_id: UUID,
    body: VesselBdnUpdate,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    bdn = await VesselBdnService.update_vessel_bdn(bdn_id, body, current_user, db)
    data = VesselBdnOut.model_validate(bdn).model_dump()
    data["generated_by_name"] = getattr(bdn, "_generated_by_name", None)
    return StandardResponse.ok(data=data, message="Vessel BDN updated")


@router.post("/vessel-bdns/{bdn_id}/approve", response_model=StandardResponse)
async def approve_vessel_bdn(
    bdn_id: UUID,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Approves this vessel run's BDN. The operation only reaches
    bdn_approved once EVERY vessel run's BDN is approved — the response
    reports real progress ("N of M") either way."""
    bdn, total, approved, gate_cleared = await VesselBdnService.approve_vessel_bdn(bdn_id, current_user, db)
    message = (
        f"Vessel BDN {bdn.bdn_number} approved — all {total} vessel run(s) now approved, operation ready to complete"
        if gate_cleared else
        f"Vessel BDN {bdn.bdn_number} approved — {approved} of {total} vessel run(s) approved so far"
    )
    data = VesselBdnOut.model_validate(bdn).model_dump()
    data["generated_by_name"] = getattr(bdn, "_generated_by_name", None)
    data["total_vessel_runs"] = total
    data["approved_vessel_runs"] = approved
    data["operation_completed_gate_cleared"] = gate_cleared
    return StandardResponse.ok(data=data, message=message)


@router.post("/vessel-bdns/{bdn_id}/reject", response_model=StandardResponse)
async def reject_vessel_bdn(
    bdn_id: UUID,
    body: VesselBdnRejectRequest,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    bdn = await VesselBdnService.reject_vessel_bdn(bdn_id, body.reason, current_user, db)
    data = VesselBdnOut.model_validate(bdn).model_dump()
    data["generated_by_name"] = getattr(bdn, "_generated_by_name", None)
    return StandardResponse.ok(data=data, message="Vessel BDN rejected")
