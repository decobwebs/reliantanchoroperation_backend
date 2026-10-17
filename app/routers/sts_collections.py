"""Ship-to-ship collections — see app/services/sts_collection_service.py.

Recording (create, stages, HSE, submitting figures) is open to the same
roles that record terminal loading receipts. Approving, rejecting,
correcting figures and cancelling are operation-manager actions, enforced
in the service.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_operation_manager, require_roles
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.common import StandardResponse
from app.schemas.sts_collection import (
    StsCollectionCreate, StsCollectionDetailsUpdate, StsCollectionOut, StsFigures,
    StsFiguresCorrection, StsHseRecord, StsReasonRequest, StsStageAdvance,
)
from app.services.sts_collection_service import StsCollectionService as svc

router = APIRouter(tags=["Ship-to-Ship Collections"])

_record_roles = Depends(require_roles(
    UserRole.bunker_manager, UserRole.ops_supervisor, UserRole.cargo_superintendent, UserRole.marine_operator,
))
_view_roles = Depends(require_roles(
    UserRole.bunker_manager, UserRole.ops_supervisor, UserRole.cargo_superintendent,
    UserRole.marine_operator, UserRole.finance_manager,
))
_manager = Depends(require_operation_manager())


def _out(c) -> dict:
    return StsCollectionOut.model_validate(c).model_dump()


@router.get("/operations/{operation_id}/sts-collections", response_model=StandardResponse)
async def list_sts_collections(operation_id: UUID, current_user: User = _view_roles, db: AsyncSession = Depends(get_db)):
    return StandardResponse.ok(data=[_out(c) for c in await svc.list_for_operation(operation_id, db)])


@router.post("/operations/{operation_id}/sts-collections", response_model=StandardResponse, status_code=status.HTTP_201_CREATED)
async def create_sts_collection(operation_id: UUID, body: StsCollectionCreate, current_user: User = _record_roles, db: AsyncSession = Depends(get_db)):
    return StandardResponse.ok(data=_out(await svc.create(operation_id, body, current_user, db)), message="Ship-to-ship collection added")


@router.patch("/sts-collections/{collection_id}", response_model=StandardResponse)
async def update_sts_collection(collection_id: UUID, body: StsCollectionDetailsUpdate, current_user: User = _record_roles, db: AsyncSession = Depends(get_db)):
    return StandardResponse.ok(data=_out(await svc.update_details(collection_id, body, current_user, db)), message="Collection updated")


@router.post("/sts-collections/{collection_id}/stage", response_model=StandardResponse)
async def advance_sts_stage(collection_id: UUID, body: StsStageAdvance, current_user: User = _record_roles, db: AsyncSession = Depends(get_db)):
    return StandardResponse.ok(data=_out(await svc.advance_stage(collection_id, body, current_user, db)), message="Stage recorded")


@router.post("/sts-collections/{collection_id}/hse", response_model=StandardResponse)
async def record_sts_hse(collection_id: UUID, body: StsHseRecord, current_user: User = _record_roles, db: AsyncSession = Depends(get_db)):
    return StandardResponse.ok(data=_out(await svc.record_hse(collection_id, body, current_user, db)), message="HSE checklist recorded")


@router.post("/sts-collections/{collection_id}/receipt", response_model=StandardResponse)
async def submit_sts_receipt(collection_id: UUID, body: StsFigures, current_user: User = _record_roles, db: AsyncSession = Depends(get_db)):
    return StandardResponse.ok(data=_out(await svc.submit_figures(collection_id, body, current_user, db)), message="Receipt submitted for approval")


@router.post("/sts-collections/{collection_id}/approve", response_model=StandardResponse)
async def approve_sts_receipt(collection_id: UUID, current_user: User = _manager, db: AsyncSession = Depends(get_db)):
    return StandardResponse.ok(data=_out(await svc.approve(collection_id, current_user, db)), message="Receipt approved — barge ROB updated")


@router.post("/sts-collections/{collection_id}/reject", response_model=StandardResponse)
async def reject_sts_receipt(collection_id: UUID, body: StsReasonRequest, current_user: User = _manager, db: AsyncSession = Depends(get_db)):
    return StandardResponse.ok(data=_out(await svc.reject(collection_id, body.reason, current_user, db)), message="Receipt rejected")


@router.put("/sts-collections/{collection_id}/receipt", response_model=StandardResponse)
async def correct_sts_receipt(collection_id: UUID, body: StsFiguresCorrection, current_user: User = _manager, db: AsyncSession = Depends(get_db)):
    return StandardResponse.ok(data=_out(await svc.correct_figures(collection_id, body, current_user, db)), message="Receipt figures corrected")


@router.post("/sts-collections/{collection_id}/cancel", response_model=StandardResponse)
async def cancel_sts_collection(collection_id: UUID, body: StsReasonRequest, current_user: User = _manager, db: AsyncSession = Depends(get_db)):
    return StandardResponse.ok(data=_out(await svc.cancel(collection_id, body.reason, current_user, db)), message="Collection cancelled")
