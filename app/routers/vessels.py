from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse, PaginatedResponse
from app.schemas.vessel import (
    VesselCreate, VesselUpdate, VesselOut,
    RobEntryCreate, RobEntryOut, RobSummaryOut,
)
from app.services.vessel_service import VesselService

router = APIRouter(prefix="/vessels", tags=["Vessels"])


@router.get("", response_model=StandardResponse)
async def list_vessels(
    current_user: User = Depends(
        require_roles(
            UserRole.bunker_manager,
            UserRole.ops_supervisor,
            UserRole.marine_manager,
            UserRole.logistics_officer,
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """List all active vessels."""
    vessels = await VesselService.list_vessels(db)
    items = [VesselOut.model_validate(v).model_dump() for v in vessels]
    return StandardResponse.ok(data=items, message="Vessels retrieved")


@router.post("", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def create_vessel(
    body: VesselCreate,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Register a new vessel. Bunker Manager only."""
    vessel = await VesselService.create_vessel(body, current_user, db)
    return StandardResponse.ok(
        data=VesselOut.model_validate(vessel).model_dump(),
        message=f"Vessel {vessel.vessel_name} created",
    )


@router.put("/{vessel_id}", response_model=StandardResponse)
async def update_vessel(
    vessel_id: UUID,
    body: VesselUpdate,
    current_user: User = Depends(require_roles(UserRole.bunker_manager)),
    db: AsyncSession = Depends(get_db),
):
    """Update vessel details. Bunker Manager only."""
    vessel = await VesselService.update_vessel(vessel_id, body, current_user, db)
    return StandardResponse.ok(
        data=VesselOut.model_validate(vessel).model_dump(),
        message="Vessel updated",
    )


@router.get("/{vessel_id}/rob", response_model=PaginatedResponse)
async def get_rob_ledger(
    vessel_id: UUID,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.ops_supervisor)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Get paginated ROB ledger for a vessel. Immutable — no updates."""
    entries, total = await VesselService.get_rob_ledger(vessel_id, page, per_page, db)
    items = [RobEntryOut.model_validate(e).model_dump() for e in entries]
    return PaginatedResponse.ok(items=items, total=total, page=page, per_page=per_page)


@router.post(
    "/{vessel_id}/rob",
    response_model=StandardResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_rob_entry(
    vessel_id: UUID,
    body: RobEntryCreate,
    current_user: User = Depends(require_roles(UserRole.ops_supervisor)),
    db: AsyncSession = Depends(get_db),
):
    """Record a new ROB entry. Ops Supervisor only. Discharge quantity is negated server-side."""
    entry = await VesselService.record_rob_entry(vessel_id, body, current_user, db)
    return StandardResponse.ok(
        data=RobEntryOut.model_validate(entry).model_dump(),
        message="ROB entry recorded",
    )


@router.get("/{vessel_id}/rob/summary", response_model=StandardResponse)
async def get_rob_summary(
    vessel_id: UUID,
    current_user: User = Depends(
        require_roles(UserRole.bunker_manager, UserRole.ops_supervisor)
    ),
    db: AsyncSession = Depends(get_db),
):
    """Get ROB summary with current level, threshold and trend data."""
    summary = await VesselService.get_rob_summary(vessel_id, db)
    return StandardResponse.ok(data=summary.model_dump(), message="ROB summary retrieved")


@router.get("/{vessel_id}", response_model=StandardResponse)
async def get_vessel(
    vessel_id: UUID,
    current_user: User = Depends(
        require_roles(
            UserRole.bunker_manager,
            UserRole.ops_supervisor,
            UserRole.marine_manager,
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """Get a single vessel by ID."""
    vessel = await VesselService.get_vessel(vessel_id, db)
    return StandardResponse.ok(data=VesselOut.model_validate(vessel).model_dump(), message="Vessel retrieved")


@router.get("/{vessel_id}/cargo-ledger", response_model=StandardResponse)
async def get_cargo_ledger(
    vessel_id: UUID,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(
        require_roles(
            UserRole.bunker_manager,
            UserRole.ops_supervisor,
            UserRole.marine_manager,
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """Enriched cargo ledger: ROB entries with linked operation, trucks, BDN, finance, and document count."""
    result = await VesselService.get_cargo_ledger(vessel_id, page, per_page, db)
    return StandardResponse.ok(data=result, message="Cargo ledger retrieved")


@router.get("/{vessel_id}/bdns", response_model=StandardResponse)
async def get_vessel_bdns(
    vessel_id: UUID,
    current_user: User = Depends(
        require_roles(
            UserRole.bunker_manager,
            UserRole.marine_manager,
            UserRole.ops_supervisor,
        )
    ),
    db: AsyncSession = Depends(get_db),
):
    """All BDNs for a vessel with actor names and operation info."""
    result = await VesselService.get_vessel_bdns(vessel_id, db)
    return StandardResponse.ok(data=result, message="Vessel BDNs retrieved")
