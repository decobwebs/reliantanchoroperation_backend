from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_roles
from app.models.user import User
from app.models.enums import UserRole
from app.schemas.common import StandardResponse
from app.schemas.licence import (
    PpdlCreate, PpdlOut, PpdlProductQuantityUpdate, PpdlProductOut,
    BflCreate, BflUpdate, BflOut, BflDeactivateRequest,
    NavalClearanceCreate, NavalClearanceOut, NavalClearanceLoadingLocationAdd,
    NavalClearanceVesselCreate, NavalClearanceRemoveWithReason,
)
from app.schemas.eta import SetEtaRequest, VesselEtaOut
from app.services.licence_service import PpdlService, BflService, NavalClearanceService
from app.services.eta_service import EtaService

router = APIRouter(tags=["Licences"])

_marine_bm = Depends(require_roles(UserRole.marine_manager, UserRole.bunker_manager))
_bm_only = Depends(require_roles(UserRole.bunker_manager))


# ── PPDL ─────────────────────────────────────────────────────────────────────

@router.post("/ppdls", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def create_ppdl(
    body: PpdlCreate,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    """Issue a new PPDL. Becomes the current PPDL — every operation created
    from now on automatically carries it."""
    ppdl = await PpdlService.create_ppdl(body, current_user, db)
    return StandardResponse.ok(data=PpdlOut.model_validate(ppdl).model_dump(), message=f"PPDL {ppdl.ppdl_number} created")


@router.get("/ppdls", response_model=StandardResponse)
async def list_ppdls(
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    ppdls = await PpdlService.list_ppdls(db)
    return StandardResponse.ok(data=[PpdlOut.model_validate(p).model_dump() for p in ppdls])


@router.get("/ppdls/{ppdl_id}", response_model=StandardResponse)
async def get_ppdl(
    ppdl_id: UUID,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    ppdl = await PpdlService.get_ppdl(ppdl_id, db)
    return StandardResponse.ok(data=PpdlOut.model_validate(ppdl).model_dump())


@router.put("/ppdls/{ppdl_id}/products/{product_id}", response_model=StandardResponse)
async def update_ppdl_product(
    ppdl_id: UUID,
    product_id: UUID,
    body: PpdlProductQuantityUpdate,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    """Raise (or correct) a product's total quantity on a PPDL. Existing
    drawdowns aren't rewritten — the balance simply recalculates."""
    product = await PpdlService.update_product_quantity(ppdl_id, product_id, body, current_user, db)
    return StandardResponse.ok(data=PpdlProductOut.model_validate(product).model_dump(), message="PPDL product updated")


# ── BFL ──────────────────────────────────────────────────────────────────────

@router.post("/bfls", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def create_bfl(
    body: BflCreate,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    """Issue a new BFL, drawing down from the current PPDL's matching product balance."""
    bfl = await BflService.create_bfl(body, current_user, db)
    return StandardResponse.ok(data=BflOut.model_validate(bfl).model_dump(), message=f"BFL {bfl.bfl_number} created")


@router.get("/bfls", response_model=StandardResponse)
async def list_bfls(
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    bfls = await BflService.list_bfls(active_only=False, db=db)
    return StandardResponse.ok(data=[BflOut.model_validate(b).model_dump() for b in bfls])


@router.get("/bfls/active", response_model=StandardResponse)
async def list_active_bfls(
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    """Non-expired, active BFLs — the picker for building a Naval Clearance."""
    bfls = await BflService.list_bfls(active_only=True, db=db)
    return StandardResponse.ok(data=[BflOut.model_validate(b).model_dump() for b in bfls])


@router.get("/bfls/{bfl_id}", response_model=StandardResponse)
async def get_bfl(
    bfl_id: UUID,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    bfl = await BflService.get_bfl(bfl_id, db)
    return StandardResponse.ok(data=BflOut.model_validate(bfl).model_dump())


@router.put("/bfls/{bfl_id}", response_model=StandardResponse)
async def update_bfl(
    bfl_id: UUID,
    body: BflUpdate,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    bfl = await BflService.update_bfl(bfl_id, body, current_user, db)
    return StandardResponse.ok(data=BflOut.model_validate(bfl).model_dump(), message="BFL updated")


@router.delete("/bfls/{bfl_id}", response_model=StandardResponse)
async def deactivate_bfl(
    bfl_id: UUID,
    body: BflDeactivateRequest,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    bfl = await BflService.deactivate_bfl(bfl_id, body.reason, current_user, db)
    return StandardResponse.ok(data=BflOut.model_validate(bfl).model_dump(), message="BFL deactivated")


# ── Naval Clearance ──────────────────────────────────────────────────────────

@router.post("/naval-clearances", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def create_naval_clearance(
    body: NavalClearanceCreate,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    nc = await NavalClearanceService.create_naval_clearance(body, current_user, db)
    return StandardResponse.ok(data=NavalClearanceOut.model_validate(nc).model_dump(), message=f"Naval Clearance {nc.clearance_number} created")


@router.get("/naval-clearances", response_model=StandardResponse)
async def list_naval_clearances(
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    ncs = await NavalClearanceService.list_naval_clearances(db)
    return StandardResponse.ok(data=[NavalClearanceOut.model_validate(nc).model_dump() for nc in ncs])


@router.get("/naval-clearances/{nc_id}", response_model=StandardResponse)
async def get_naval_clearance(
    nc_id: UUID,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    nc = await NavalClearanceService.get_naval_clearance(nc_id, db)
    return StandardResponse.ok(data=NavalClearanceOut.model_validate(nc).model_dump())


@router.post("/naval-clearances/{nc_id}/loading-locations", response_model=StandardResponse)
async def add_loading_location(
    nc_id: UUID,
    body: NavalClearanceLoadingLocationAdd,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    nc = await NavalClearanceService.add_loading_location(nc_id, body, current_user, db)
    return StandardResponse.ok(data=NavalClearanceOut.model_validate(nc).model_dump(), message="Loading location added")


@router.delete("/naval-clearances/{nc_id}/loading-locations/{location_id}", response_model=StandardResponse)
async def remove_loading_location(
    nc_id: UUID,
    location_id: UUID,
    body: NavalClearanceRemoveWithReason,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    nc = await NavalClearanceService.remove_loading_location(nc_id, location_id, body.reason, current_user, db)
    return StandardResponse.ok(data=NavalClearanceOut.model_validate(nc).model_dump(), message="Loading location removed")


@router.post("/naval-clearances/{nc_id}/vessels", response_model=StandardResponse)
async def add_vessel(
    nc_id: UUID,
    body: NavalClearanceVesselCreate,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    nc = await NavalClearanceService.add_vessel(nc_id, body, current_user, db)
    return StandardResponse.ok(data=NavalClearanceOut.model_validate(nc).model_dump(), message="Vessel added")


@router.delete("/naval-clearances/{nc_id}/vessels/{vessel_id}", response_model=StandardResponse)
async def remove_vessel(
    nc_id: UUID,
    vessel_id: UUID,
    body: NavalClearanceRemoveWithReason,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    nc = await NavalClearanceService.remove_vessel(nc_id, vessel_id, body.reason, current_user, db)
    return StandardResponse.ok(data=NavalClearanceOut.model_validate(nc).model_dump(), message="Vessel removed")


@router.post("/naval-clearances/{nc_id}/documents/upload", response_model=StandardResponse)
async def upload_naval_clearance_document(
    nc_id: UUID,
    file: UploadFile = File(...),
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    mime_type = file.content_type or "application/octet-stream"
    nc = await NavalClearanceService.upload_document(
        nc_id, content, file.filename or "document", mime_type, current_user, db,
    )
    return StandardResponse.ok(data=NavalClearanceOut.model_validate(nc).model_dump(), message="Document uploaded")


# ── Vessel ETA — append-only per client-vessel ──────────────────────────────

@router.post("/naval-clearance-vessels/{ncv_id}/eta", response_model=StandardResponse)
async def set_eta(
    ncv_id: UUID,
    body: SetEtaRequest,
    current_user: User = _bm_only,
    db: AsyncSession = Depends(get_db),
):
    """Sets a new ETA — never overwrites the previous one, so planned vs.
    actual can be compared later. Does not auto-notify the client; the
    change surfaces as a draft in the tick-to-send screen."""
    eta = await EtaService.set_eta(ncv_id, body, current_user, db)
    return StandardResponse.ok(data=VesselEtaOut.model_validate(eta).model_dump(), message="ETA recorded")


@router.get("/naval-clearance-vessels/{ncv_id}/eta-history", response_model=StandardResponse)
async def get_eta_history(
    ncv_id: UUID,
    current_user: User = _marine_bm,
    db: AsyncSession = Depends(get_db),
):
    history = await EtaService.get_eta_history(ncv_id, db)
    return StandardResponse.ok(data=[VesselEtaOut.model_validate(e).model_dump() for e in history])
