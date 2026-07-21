from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse, PaginatedResponse
from app.schemas.truck_bdn import (
    TruckBdnCreate, TruckBdnUpdate, TruckBdnOut, TruckBdnApproveRequest, TruckBdnRejectRequest,
)
from app.services.truck_bdn_service import TruckBdnService

router = APIRouter(tags=["Truck BDNs"])


@router.get("/operations/{operation_id}/truck-bdns", response_model=StandardResponse)
async def list_truck_bdns(
    operation_id: UUID,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.ops_supervisor, UserRole.logistics_officer, UserRole.finance_manager)
    ),
    db: AsyncSession = Depends(get_db),
):
    """List Truck BDNs for an operation."""
    truck_bdns = await TruckBdnService.list_truck_bdns(operation_id, db)
    items = [TruckBdnOut.model_validate(b).model_dump() for b in truck_bdns]
    return StandardResponse.ok(data=items, message="Truck BDNs retrieved")


@router.post(
    "/operations/{operation_id}/truck-bdns",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_truck_bdn(
    operation_id: UUID,
    body: TruckBdnCreate,
    current_user: User = Depends(require_roles(UserRole.ops_supervisor, UserRole.logistics_officer)),
    db: AsyncSession = Depends(get_db),
):
    """Submit a Truck BDN for an operation. Ops Supervisor or Logistics Officer only."""
    truck_bdn = await TruckBdnService.create_truck_bdn(operation_id, body, current_user, db)

    data = TruckBdnOut.model_validate(truck_bdn).model_dump()
    data["generated_by_name"] = getattr(truck_bdn, "_generated_by_name", None)

    return StandardResponse.ok(data=data, message=f"Truck BDN {truck_bdn.truck_bdn_number} created")


@router.get("/truck-bdns/{truck_bdn_id}", response_model=StandardResponse)
async def get_truck_bdn(
    truck_bdn_id: UUID,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.ops_supervisor, UserRole.logistics_officer, UserRole.finance_manager)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Get a single Truck BDN by ID."""
    truck_bdn = await TruckBdnService.get_truck_bdn(truck_bdn_id, db)
    return StandardResponse.ok(
        data=TruckBdnOut.model_validate(truck_bdn).model_dump(),
        message="Truck BDN retrieved",
    )


@router.put("/truck-bdns/{truck_bdn_id}", response_model=StandardResponse)
async def update_truck_bdn(
    truck_bdn_id: UUID,
    body: TruckBdnUpdate,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Edit any field on a Truck BDN. Requires a reason. Bunker Manager only."""
    truck_bdn = await TruckBdnService.update_truck_bdn(truck_bdn_id, body, current_user, db)
    return StandardResponse.ok(
        data=TruckBdnOut.model_validate(truck_bdn).model_dump(),
        message=f"Truck BDN {truck_bdn.truck_bdn_number} updated",
    )


@router.post("/truck-bdns/{truck_bdn_id}/approve", response_model=StandardResponse)
async def approve_truck_bdn(
    truck_bdn_id: UUID,
    body: TruckBdnApproveRequest = TruckBdnApproveRequest(),
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Approve a Truck BDN. Bunker Manager only."""
    truck_bdn = await TruckBdnService.approve_truck_bdn(truck_bdn_id, current_user, db)
    return StandardResponse.ok(
        data=TruckBdnOut.model_validate(truck_bdn).model_dump(),
        message=f"Truck BDN {truck_bdn.truck_bdn_number} approved",
    )


@router.post("/truck-bdns/{truck_bdn_id}/reject", response_model=StandardResponse)
async def reject_truck_bdn(
    truck_bdn_id: UUID,
    body: TruckBdnRejectRequest,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Reject a Truck BDN with a reason. Bunker Manager only."""
    truck_bdn = await TruckBdnService.reject_truck_bdn(truck_bdn_id, body.reason, current_user, db)
    return StandardResponse.ok(
        data=TruckBdnOut.model_validate(truck_bdn).model_dump(),
        message=f"Truck BDN {truck_bdn.truck_bdn_number} rejected",
    )


@router.get("/truck-bdns", response_model=PaginatedResponse)
async def get_all_truck_bdns(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Global Truck BDN register — all operations. Bunker Manager only."""
    truck_bdns, total = await TruckBdnService.get_all_truck_bdns(page, per_page, db)
    items = [TruckBdnOut.model_validate(b).model_dump() for b in truck_bdns]
    return PaginatedResponse.ok(items=items, total=total, page=page, per_page=per_page)
