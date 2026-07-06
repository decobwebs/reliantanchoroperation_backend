from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse, PaginatedResponse
from app.schemas.bdn import BdnCreate, BdnOut, BdnApproveRequest, BdnRejectRequest
from app.services.bdn_service import BdnService

router = APIRouter(tags=["BDNs"])


@router.get("/operations/{operation_id}/bdns", response_model=StandardResponse)
async def list_bdns(
    operation_id: UUID,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.marine_manager, UserRole.finance_manager)
    ),
    db: AsyncSession = Depends(get_db),
):
    """List BDNs for an operation."""
    bdns = await BdnService.list_bdns(operation_id, db)
    items = [BdnOut.model_validate(b).model_dump() for b in bdns]
    return StandardResponse.ok(data=items, message="BDNs retrieved")


@router.post(
    "/operations/{operation_id}/bdns",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_bdn(
    operation_id: UUID,
    body: BdnCreate,
    current_user: User = Depends(require_roles(UserRole.marine_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Create a BDN for an operation. Marine Manager only."""
    bdn = await BdnService.create_bdn(operation_id, body, current_user, db)

    # Build out schema with computed fields
    bdn_data = BdnOut.model_validate(bdn).model_dump()
    bdn_data["vessel_name"] = getattr(bdn, "_vessel_name", None)
    bdn_data["generated_by_name"] = getattr(bdn, "_generated_by_name", None)

    return StandardResponse.ok(data=bdn_data, message=f"BDN {bdn.bdn_number} created")


@router.get("/bdns/{bdn_id}", response_model=StandardResponse)
async def get_bdn(
    bdn_id: UUID,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.marine_manager)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Get a single BDN by ID."""
    bdn = await BdnService.get_bdn(bdn_id, db)
    return StandardResponse.ok(
        data=BdnOut.model_validate(bdn).model_dump(),
        message="BDN retrieved",
    )


@router.post("/bdns/{bdn_id}/approve", response_model=StandardResponse)
async def approve_bdn(
    bdn_id: UUID,
    body: BdnApproveRequest = BdnApproveRequest(),
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Approve a BDN. Bunker Manager only."""
    bdn = await BdnService.approve_bdn(bdn_id, current_user, db)
    return StandardResponse.ok(
        data=BdnOut.model_validate(bdn).model_dump(),
        message=f"BDN {bdn.bdn_number} approved",
    )


@router.post("/bdns/{bdn_id}/reject", response_model=StandardResponse)
async def reject_bdn(
    bdn_id: UUID,
    body: BdnRejectRequest,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Reject a BDN with a reason. Bunker Manager only."""
    bdn = await BdnService.reject_bdn(bdn_id, body.reason, current_user, db)
    return StandardResponse.ok(
        data=BdnOut.model_validate(bdn).model_dump(),
        message=f"BDN {bdn.bdn_number} rejected",
    )


@router.get("/bdns", response_model=PaginatedResponse)
async def get_all_bdns(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Global BDN register — all operations. Bunker Manager only."""
    bdns, total = await BdnService.get_all_bdns(page, per_page, db)
    items = [BdnOut.model_validate(b).model_dump() for b in bdns]
    return PaginatedResponse.ok(items=items, total=total, page=page, per_page=per_page)
