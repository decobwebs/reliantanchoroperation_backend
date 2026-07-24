from typing import List, Optional
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.licence import (
    Ppdl, PpdlProduct, Bfl, NavalClearance, NavalClearanceDrawdown,
    NavalClearanceLoadingLocation, NavalClearanceVessel,
)
from app.models.user import User
from app.models.audit import AuditLog
from app.schemas.licence import (
    PpdlCreate, PpdlProductQuantityUpdate,
    BflCreate, BflUpdate,
    NavalClearanceCreate, NavalClearanceLoadingLocationAdd, NavalClearanceVesselCreate,
)
from app.services.audit_diff import capture_diff
from app.services.document_service import _upload_to_supabase


async def _get_ppdl_or_404(ppdl_id: UUID, db: AsyncSession) -> Ppdl:
    result = await db.execute(
        select(Ppdl).options(selectinload(Ppdl.products)).where(Ppdl.id == ppdl_id)
    )
    ppdl = result.scalar_one_or_none()
    if not ppdl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PPDL not found")
    return ppdl


async def _get_current_ppdl_or_404(db: AsyncSession) -> Ppdl:
    result = await db.execute(select(Ppdl).where(Ppdl.is_current == True))  # noqa: E712
    ppdl = result.scalar_one_or_none()
    if not ppdl:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No current PPDL exists — Marine must create one before issuing a BFL",
        )
    return ppdl


async def _get_bfl_or_404(bfl_id: UUID, db: AsyncSession) -> Bfl:
    result = await db.execute(
        select(Bfl).options(selectinload(Bfl.ppdl)).where(Bfl.id == bfl_id)
    )
    bfl = result.scalar_one_or_none()
    if not bfl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="BFL not found")
    return bfl


def _nc_query():
    # populate_existing forces already-identity-mapped rows (and their
    # eagerly-loaded collections) to refresh from the DB — without it, a
    # fetch-after-mutation on an object already loaded earlier in this
    # session would silently return the stale, pre-mutation collection.
    return select(NavalClearance).execution_options(populate_existing=True).options(
        selectinload(NavalClearance.drawdowns).selectinload(NavalClearanceDrawdown.bfl).selectinload(Bfl.ppdl),
        selectinload(NavalClearance.loading_locations),
        selectinload(NavalClearance.vessels).selectinload(NavalClearanceVessel.client),
    )


def _attach_naval_clearance_display_fields(nc: NavalClearance) -> NavalClearance:
    """Attach transient, computed-only display fields — ppdl_number/bfl_numbers/
    products/total_quantity_litres/is_valid aren't real columns; they're always
    derived from the already-eager-loaded drawdown/bfl/vessel relationships."""
    nc.is_valid = NavalClearanceService.is_valid_for_operations(nc)
    nc.total_quantity_litres = sum((d.quantity_litres for d in nc.drawdowns), Decimal("0"))

    ppdl_numbers = {d.bfl.ppdl.ppdl_number for d in nc.drawdowns if d.bfl and d.bfl.ppdl}
    nc.ppdl_number = next(iter(ppdl_numbers), None)
    nc.bfl_numbers = sorted({d.bfl.bfl_number for d in nc.drawdowns if d.bfl})
    nc.products = sorted({d.bfl.product_type for d in nc.drawdowns if d.bfl})

    for d in nc.drawdowns:
        d.bfl_number = d.bfl.bfl_number if d.bfl else None
        d.product_type = d.bfl.product_type if d.bfl else None

    for v in nc.vessels:
        v.client_name = v.client.full_name if v.client else None
        v.client_email = v.client.email if v.client else None
        v.current_eta = None  # populated once the ETA table lands (Part 3)

    return nc


async def _get_naval_clearance_or_404(nc_id: UUID, db: AsyncSession) -> NavalClearance:
    result = await db.execute(_nc_query().where(NavalClearance.id == nc_id))
    nc = result.scalar_one_or_none()
    if not nc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Naval Clearance not found")
    return _attach_naval_clearance_display_fields(nc)


async def _ppdl_product_balance(ppdl_id: UUID, product_type: str, db: AsyncSession) -> Decimal:
    """Balance = the product's total quantity minus every BFL drawn against it."""
    product_result = await db.execute(
        select(PpdlProduct).where(
            and_(PpdlProduct.ppdl_id == ppdl_id, PpdlProduct.product_type == product_type)
        )
    )
    product = product_result.scalar_one_or_none()
    if not product:
        return Decimal("0")
    drawn_result = await db.execute(
        select(func.coalesce(func.sum(Bfl.quantity_litres), 0)).where(
            and_(Bfl.ppdl_id == ppdl_id, Bfl.product_type == product_type)
        )
    )
    drawn = Decimal(drawn_result.scalar() or 0)
    return product.quantity_litres - drawn


async def _bfl_balance(bfl_id: UUID, db: AsyncSession) -> Decimal:
    bfl = await _get_bfl_or_404(bfl_id, db)
    drawn_result = await db.execute(
        select(func.coalesce(func.sum(NavalClearanceDrawdown.quantity_litres), 0)).where(
            NavalClearanceDrawdown.bfl_id == bfl_id
        )
    )
    drawn = Decimal(drawn_result.scalar() or 0)
    return bfl.quantity_litres - drawn


async def _attach_ppdl_balances(ppdl: Ppdl, db: AsyncSession) -> Ppdl:
    for product in ppdl.products:
        drawn_result = await db.execute(
            select(func.coalesce(func.sum(Bfl.quantity_litres), 0)).where(
                and_(Bfl.ppdl_id == ppdl.id, Bfl.product_type == product.product_type)
            )
        )
        drawn = Decimal(drawn_result.scalar() or 0)
        product.allocated_litres = drawn
        product.remaining_litres = product.quantity_litres - drawn
    return ppdl


async def _attach_bfl_balance(bfl: Bfl, db: AsyncSession) -> Bfl:
    drawn_result = await db.execute(
        select(func.coalesce(func.sum(NavalClearanceDrawdown.quantity_litres), 0)).where(
            NavalClearanceDrawdown.bfl_id == bfl.id
        )
    )
    drawn = Decimal(drawn_result.scalar() or 0)
    bfl.allocated_litres = drawn
    bfl.remaining_litres = bfl.quantity_litres - drawn
    bfl.ppdl_number = bfl.ppdl.ppdl_number if bfl.ppdl else None
    return bfl


class PpdlService:

    @staticmethod
    async def create_ppdl(data: PpdlCreate, current_user: User, db: AsyncSession) -> Ppdl:
        existing_result = await db.execute(select(Ppdl).where(Ppdl.ppdl_number == data.ppdl_number))
        if existing_result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A PPDL with this number already exists")

        # Flip whichever PPDL is currently marked current — only one may hold that flag.
        await db.execute(
            Ppdl.__table__.update().where(Ppdl.is_current == True).values(is_current=False)  # noqa: E712
        )

        ppdl = Ppdl(
            ppdl_number=data.ppdl_number, issue_date=data.issue_date, expiry_date=data.expiry_date,
            is_current=True, created_by=current_user.id,
        )
        db.add(ppdl)
        await db.flush()

        for p in data.products:
            db.add(PpdlProduct(ppdl_id=ppdl.id, product_type=p.product_type, quantity_litres=p.quantity_litres))

        db.add(AuditLog(
            user_id=current_user.id, action="CREATE_PPDL", entity_type="ppdl", entity_id=ppdl.id,
            changes={"ppdl_number": data.ppdl_number, "products": [
                {"product_type": p.product_type, "quantity_litres": str(p.quantity_litres)} for p in data.products
            ]},
        ))

        await db.flush()
        await db.refresh(ppdl)
        return await _get_ppdl_or_404(ppdl.id, db)

    @staticmethod
    async def list_ppdls(db: AsyncSession) -> List[Ppdl]:
        result = await db.execute(
            select(Ppdl).options(selectinload(Ppdl.products)).order_by(Ppdl.created_at.desc())
        )
        ppdls = result.scalars().all()
        for p in ppdls:
            await _attach_ppdl_balances(p, db)
        return ppdls

    @staticmethod
    async def get_ppdl(ppdl_id: UUID, db: AsyncSession) -> Ppdl:
        ppdl = await _get_ppdl_or_404(ppdl_id, db)
        return await _attach_ppdl_balances(ppdl, db)

    @staticmethod
    async def update_product_quantity(
        ppdl_id: UUID, product_id: UUID, data: PpdlProductQuantityUpdate, current_user: User, db: AsyncSession,
    ) -> PpdlProduct:
        result = await db.execute(
            select(PpdlProduct).where(
                and_(PpdlProduct.id == product_id, PpdlProduct.ppdl_id == ppdl_id)
            )
        )
        product = result.scalar_one_or_none()
        if not product:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PPDL product not found")

        changes = capture_diff(product, {"quantity_litres": data.quantity_litres})
        db.add(AuditLog(
            user_id=current_user.id, action="UPDATE_PPDL_PRODUCT", entity_type="ppdl_product",
            entity_id=product.id, changes=changes, reason=data.reason,
        ))
        await db.flush()
        await db.refresh(product)

        drawn_result = await db.execute(
            select(func.coalesce(func.sum(Bfl.quantity_litres), 0)).where(
                and_(Bfl.ppdl_id == ppdl_id, Bfl.product_type == product.product_type)
            )
        )
        drawn = Decimal(drawn_result.scalar() or 0)
        product.allocated_litres = drawn
        product.remaining_litres = product.quantity_litres - drawn
        return product


class BflService:

    @staticmethod
    async def create_bfl(data: BflCreate, current_user: User, db: AsyncSession) -> Bfl:
        existing_result = await db.execute(select(Bfl).where(Bfl.bfl_number == data.bfl_number))
        if existing_result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A BFL with this number already exists")

        ppdl = await _get_current_ppdl_or_404(db)

        remaining = await _ppdl_product_balance(ppdl.id, data.product_type, db)
        if data.quantity_litres > remaining:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Only {remaining} L remaining on the current PPDL for {data.product_type}",
            )

        bfl = Bfl(
            bfl_number=data.bfl_number, ppdl_id=ppdl.id, product_type=data.product_type,
            quantity_litres=data.quantity_litres, vessel=data.vessel, expiry_date=data.expiry_date,
            created_by=current_user.id,
        )
        db.add(bfl)
        await db.flush()

        db.add(AuditLog(
            user_id=current_user.id, action="CREATE_BFL", entity_type="bfl", entity_id=bfl.id,
            changes={
                "bfl_number": data.bfl_number, "ppdl_number": ppdl.ppdl_number,
                "product_type": data.product_type, "quantity_litres": str(data.quantity_litres),
            },
        ))
        await db.flush()
        await db.refresh(bfl)
        bfl.ppdl = ppdl  # already loaded in this session — avoids a lazy-load
        return await _attach_bfl_balance(bfl, db)

    @staticmethod
    async def list_bfls(active_only: bool, db: AsyncSession) -> List[Bfl]:
        stmt = select(Bfl).options(selectinload(Bfl.ppdl)).order_by(Bfl.created_at.desc())
        if active_only:
            stmt = stmt.where(and_(Bfl.is_active == True, Bfl.expiry_date >= date.today()))  # noqa: E712
        result = await db.execute(stmt)
        bfls = result.scalars().all()
        for b in bfls:
            await _attach_bfl_balance(b, db)
        return bfls

    @staticmethod
    async def get_bfl(bfl_id: UUID, db: AsyncSession) -> Bfl:
        bfl = await _get_bfl_or_404(bfl_id, db)
        return await _attach_bfl_balance(bfl, db)

    @staticmethod
    async def update_bfl(bfl_id: UUID, data: BflUpdate, current_user: User, db: AsyncSession) -> Bfl:
        bfl = await _get_bfl_or_404(bfl_id, db)
        update_data = data.model_dump(exclude_unset=True, exclude={"reason"})

        if "quantity_litres" in update_data:
            drawn_result = await db.execute(
                select(func.coalesce(func.sum(NavalClearanceDrawdown.quantity_litres), 0)).where(
                    NavalClearanceDrawdown.bfl_id == bfl_id
                )
            )
            drawn = Decimal(drawn_result.scalar() or 0)
            if update_data["quantity_litres"] < drawn:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Cannot reduce below {drawn} L already drawn by Naval Clearances",
                )

            # Raising the quantity draws MORE from the parent PPDL product —
            # re-run the same over-draw check create_bfl enforces. The PPDL
            # balance already excludes this BFL's own current allocation, so
            # only the incremental increase needs to fit within it.
            increase = update_data["quantity_litres"] - bfl.quantity_litres
            if increase > 0:
                remaining = await _ppdl_product_balance(bfl.ppdl_id, bfl.product_type, db)
                if increase > remaining:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Only {remaining} L remaining on the parent PPDL for {bfl.product_type} — "
                               f"cannot raise this BFL by {increase} L",
                    )

        changes = capture_diff(bfl, update_data)
        db.add(AuditLog(
            user_id=current_user.id, action="UPDATE_BFL", entity_type="bfl",
            entity_id=bfl.id, changes=changes, reason=data.reason,
        ))
        await db.flush()
        await db.refresh(bfl)
        return await _attach_bfl_balance(bfl, db)

    @staticmethod
    async def deactivate_bfl(bfl_id: UUID, reason: str, current_user: User, db: AsyncSession) -> Bfl:
        bfl = await _get_bfl_or_404(bfl_id, db)
        drawn_result = await db.execute(
            select(func.count()).select_from(NavalClearanceDrawdown).where(NavalClearanceDrawdown.bfl_id == bfl_id)
        )
        if (drawn_result.scalar() or 0) > 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot deactivate a BFL that has Naval Clearance drawdowns against it",
            )
        bfl.is_active = False
        db.add(AuditLog(
            user_id=current_user.id, action="DEACTIVATE_BFL", entity_type="bfl",
            entity_id=bfl.id, changes={"is_active": {"from": "True", "to": "False"}}, reason=reason,
        ))
        await db.flush()
        await db.refresh(bfl)
        return bfl


class NavalClearanceService:

    @staticmethod
    async def create_naval_clearance(data: NavalClearanceCreate, current_user: User, db: AsyncSession) -> NavalClearance:
        existing_result = await db.execute(
            select(NavalClearance).where(NavalClearance.clearance_number == data.clearance_number)
        )
        if existing_result.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A Naval Clearance with this number already exists")

        nc = NavalClearance(
            clearance_number=data.clearance_number, date_of_loading=data.date_of_loading,
            expiry_date=data.expiry_date, created_by=current_user.id,
        )
        db.add(nc)
        await db.flush()

        drawdown_changes = []
        for d in data.drawdowns:
            remaining = await _bfl_balance(d.bfl_id, db)
            if d.quantity_litres > remaining:
                bfl = await _get_bfl_or_404(d.bfl_id, db)
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Only {remaining} L remaining on BFL {bfl.bfl_number}",
                )
            db.add(NavalClearanceDrawdown(naval_clearance_id=nc.id, bfl_id=d.bfl_id, quantity_litres=d.quantity_litres))
            drawdown_changes.append({"bfl_id": str(d.bfl_id), "quantity_litres": str(d.quantity_litres)})

        for i, loc in enumerate(data.loading_locations):
            db.add(NavalClearanceLoadingLocation(naval_clearance_id=nc.id, location=loc, sort_order=i))

        for v in data.vessels:
            db.add(NavalClearanceVessel(
                naval_clearance_id=nc.id, client_id=v.client_id,
                vessel_name=v.vessel_name, imo_number=v.imo_number,
            ))

        db.add(AuditLog(
            user_id=current_user.id, action="CREATE_NAVAL_CLEARANCE", entity_type="naval_clearance",
            entity_id=nc.id, changes={"clearance_number": data.clearance_number, "drawdowns": drawdown_changes},
        ))

        await db.flush()
        return await _get_naval_clearance_or_404(nc.id, db)

    @staticmethod
    async def list_naval_clearances(db: AsyncSession) -> List[NavalClearance]:
        result = await db.execute(_nc_query().order_by(NavalClearance.created_at.desc()))
        return [_attach_naval_clearance_display_fields(nc) for nc in result.scalars().all()]

    @staticmethod
    async def get_naval_clearance(nc_id: UUID, db: AsyncSession) -> NavalClearance:
        return await _get_naval_clearance_or_404(nc_id, db)

    @staticmethod
    def is_valid_for_operations(nc: NavalClearance) -> bool:
        """Clearance validity governs operational permission, independent of
        its parent BFL/PPDL's expiry — display-only, never a block."""
        return nc.expiry_date >= date.today()

    @staticmethod
    async def add_loading_location(nc_id: UUID, data: NavalClearanceLoadingLocationAdd, current_user: User, db: AsyncSession) -> NavalClearance:
        nc = await _get_naval_clearance_or_404(nc_id, db)
        next_order = len(nc.loading_locations)
        db.add(NavalClearanceLoadingLocation(naval_clearance_id=nc_id, location=data.location, sort_order=next_order))
        db.add(AuditLog(
            user_id=current_user.id, action="ADD_NAVAL_CLEARANCE_LOCATION", entity_type="naval_clearance",
            entity_id=nc_id, changes={"location": data.location},
        ))
        await db.flush()
        return await _get_naval_clearance_or_404(nc_id, db)

    @staticmethod
    async def remove_loading_location(nc_id: UUID, location_id: UUID, reason: str, current_user: User, db: AsyncSession) -> NavalClearance:
        result = await db.execute(
            select(NavalClearanceLoadingLocation).where(
                and_(NavalClearanceLoadingLocation.id == location_id, NavalClearanceLoadingLocation.naval_clearance_id == nc_id)
            )
        )
        loc = result.scalar_one_or_none()
        if not loc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Loading location not found")
        await db.delete(loc)
        db.add(AuditLog(
            user_id=current_user.id, action="REMOVE_NAVAL_CLEARANCE_LOCATION", entity_type="naval_clearance",
            entity_id=nc_id, changes={"location": loc.location}, reason=reason,
        ))
        await db.flush()
        return await _get_naval_clearance_or_404(nc_id, db)

    @staticmethod
    async def add_vessel(nc_id: UUID, data: NavalClearanceVesselCreate, current_user: User, db: AsyncSession) -> NavalClearance:
        await _get_naval_clearance_or_404(nc_id, db)
        db.add(NavalClearanceVessel(
            naval_clearance_id=nc_id, client_id=data.client_id,
            vessel_name=data.vessel_name, imo_number=data.imo_number,
        ))
        db.add(AuditLog(
            user_id=current_user.id, action="ADD_NAVAL_CLEARANCE_VESSEL", entity_type="naval_clearance",
            entity_id=nc_id, changes={"vessel_name": data.vessel_name, "client_id": str(data.client_id)},
        ))
        await db.flush()
        return await _get_naval_clearance_or_404(nc_id, db)

    @staticmethod
    async def remove_vessel(nc_id: UUID, vessel_id: UUID, reason: str, current_user: User, db: AsyncSession) -> NavalClearance:
        result = await db.execute(
            select(NavalClearanceVessel).where(
                and_(NavalClearanceVessel.id == vessel_id, NavalClearanceVessel.naval_clearance_id == nc_id)
            )
        )
        vessel = result.scalar_one_or_none()
        if not vessel:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel not found on this clearance")

        # Blocked once this vessel has been acted on for that client — an ETA
        # was communicated or a notification was sent. VesselEta cascades on
        # delete (would silently destroy append-only history);
        # ClientNotificationLog has no cascade at all (would raise a raw FK
        # IntegrityError). Surface both as a clean 422 instead.
        from app.models.notification_log import VesselEta, ClientNotificationLog
        eta_count = (await db.execute(
            select(func.count()).select_from(VesselEta).where(VesselEta.naval_clearance_vessel_id == vessel_id)
        )).scalar() or 0
        notification_count = (await db.execute(
            select(func.count()).select_from(ClientNotificationLog).where(ClientNotificationLog.naval_clearance_vessel_id == vessel_id)
        )).scalar() or 0
        if eta_count > 0 or notification_count > 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cannot remove this vessel — it already has ETA or client-notification history on this clearance",
            )

        await db.delete(vessel)
        db.add(AuditLog(
            user_id=current_user.id, action="REMOVE_NAVAL_CLEARANCE_VESSEL", entity_type="naval_clearance",
            entity_id=nc_id, changes={"vessel_name": vessel.vessel_name}, reason=reason,
        ))
        await db.flush()
        return await _get_naval_clearance_or_404(nc_id, db)

    @staticmethod
    async def upload_document(nc_id: UUID, file_bytes: bytes, filename: str, content_type: str, current_user: User, db: AsyncSession) -> NavalClearance:
        nc = await _get_naval_clearance_or_404(nc_id, db)
        safe_name = (filename or "document").replace("..", "").replace("/", "_").replace("\\", "_")
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        storage_path = f"naval-clearances/{nc_id}/{timestamp}_{str(uuid4())[:8]}_{safe_name}"
        url = await _upload_to_supabase(file_bytes, storage_path, content_type)
        nc.document_url = url
        db.add(AuditLog(
            user_id=current_user.id, action="UPLOAD_NAVAL_CLEARANCE_DOCUMENT", entity_type="naval_clearance",
            entity_id=nc_id, changes={"document_url": url},
        ))
        await db.flush()
        return nc
