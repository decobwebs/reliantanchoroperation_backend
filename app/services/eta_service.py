from typing import List
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status

from app.models.licence import NavalClearanceVessel
from app.models.notification_log import VesselEta
from app.models.audit import AuditLog
from app.models.user import User
from app.schemas.eta import SetEtaRequest


class EtaService:

    @staticmethod
    async def _get_ncv_or_404(naval_clearance_vessel_id: UUID, db: AsyncSession) -> NavalClearanceVessel:
        ncv = await db.get(NavalClearanceVessel, naval_clearance_vessel_id)
        if not ncv:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Naval Clearance vessel not found")
        return ncv

    @staticmethod
    async def set_eta(naval_clearance_vessel_id: UUID, data: SetEtaRequest, current_user: User, db: AsyncSession) -> VesselEta:
        """Append-only — never overwrites the previous ETA, so planned vs.
        actual can be compared later. Does NOT auto-notify the client — that
        stays behind the explicit tick-to-send confirmation screen, with no
        exception for ETA changes (spec: nothing sends without an explicit
        tick). Surface the change as a suggested draft in that screen
        instead of firing an email here."""
        await EtaService._get_ncv_or_404(naval_clearance_vessel_id, db)

        eta = VesselEta(
            naval_clearance_vessel_id=naval_clearance_vessel_id,
            eta_at=data.eta_at,
            reason=data.reason,
            set_by=current_user.id,
        )
        db.add(eta)
        await db.flush()

        db.add(AuditLog(
            user_id=current_user.id, action="SET_VESSEL_ETA", entity_type="vessel_eta", entity_id=eta.id,
            changes={"naval_clearance_vessel_id": str(naval_clearance_vessel_id), "eta_at": data.eta_at.isoformat(), "reason": data.reason},
        ))
        await db.flush()
        await db.refresh(eta)
        return eta

    @staticmethod
    async def get_eta_history(naval_clearance_vessel_id: UUID, db: AsyncSession) -> List[VesselEta]:
        await EtaService._get_ncv_or_404(naval_clearance_vessel_id, db)
        result = await db.execute(
            select(VesselEta)
            .where(VesselEta.naval_clearance_vessel_id == naval_clearance_vessel_id)
            .order_by(VesselEta.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_current_eta(naval_clearance_vessel_id: UUID, db: AsyncSession) -> "VesselEta | None":
        result = await db.execute(
            select(VesselEta)
            .where(VesselEta.naval_clearance_vessel_id == naval_clearance_vessel_id)
            .order_by(VesselEta.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
