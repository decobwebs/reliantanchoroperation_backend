import logging
from typing import List
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, and_, func
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.bdn import BDN, VesselActivity, VesselActivityLeg, RobEntry
from app.models.operation import Operation, OperationStatusHistory
from app.models.vessel import Vessel
from app.models.audit import AuditLog
from app.models.user import User
from app.models.enums import UserRole, BdnStatus, OperationStatus, VesselActivityStatus, VesselStage, VesselLegStage, OperationType, RobEntryType
from app.schemas.vessel_bdn import VesselBdnCreate, VesselBdnUpdate
from app.services.notification_service import notify
from app.services.audit_diff import capture_diff
from app.services.state_machine import StateMachine, StateMachineError, acting_role
from app.services.email_service import email_vessel_bdn_submitted
from app.utils.number_generator import generate_bdn_number

logger = logging.getLogger("raoms.vessel_bdn")


async def _get_operation_or_404(operation_id: UUID, db: AsyncSession) -> Operation:
    result = await db.execute(
        select(Operation).where(and_(Operation.id == operation_id, Operation.deleted_at.is_(None)))
    )
    operation = result.scalar_one_or_none()
    if not operation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    return operation


async def _get_vessel_activity_or_404(vessel_activity_id: UUID, db: AsyncSession) -> VesselActivity:
    activity = await db.get(VesselActivity, vessel_activity_id)
    if not activity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel activity not found")
    return activity


async def _get_vessel_activity_leg_or_404(leg_id: UUID, db: AsyncSession) -> VesselActivityLeg:
    leg = await db.get(VesselActivityLeg, leg_id)
    if not leg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receiving-vessel leg not found")
    return leg


async def _transition_operation(
    operation: Operation, to_status: OperationStatus, current_user: User, db: AsyncSession, reason: str = "",
) -> None:
    if operation.status == to_status:
        return
    try:
        StateMachine.validate_transition(
            operation.type, operation.status, to_status, acting_role(current_user)
        )
    except StateMachineError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    from_status = operation.status
    operation.status = to_status
    operation.updated_at = datetime.utcnow()

    db.add(OperationStatusHistory(
        operation_id=operation.id, from_status=from_status, to_status=to_status,
        changed_by=current_user.id, reason=reason, metadata_={},
    ))


class VesselBdnService:

    @staticmethod
    async def list_vessel_bdns(operation_id: UUID, db: AsyncSession) -> List[BDN]:
        await _get_operation_or_404(operation_id, db)
        result = await db.execute(
            select(BDN)
            .where(and_(BDN.operation_id == operation_id, BDN.vessel_activity_id.is_not(None)))
            .options(selectinload(BDN.generator))
            .order_by(BDN.created_at.desc())
        )
        bdns = list(result.scalars().all())
        for bdn in bdns:
            bdn._generated_by_name = bdn.generator.full_name if bdn.generator else None
        return bdns

    @staticmethod
    async def create_vessel_bdn(vessel_activity_id: UUID, data: VesselBdnCreate, current_user: User, db: AsyncSession) -> BDN:
        activity = await _get_vessel_activity_or_404(vessel_activity_id, db)
        operation = await _get_operation_or_404(activity.operation_id, db)

        # The one hard gate this whole flow protects: a BDN can't even be
        # submitted, let alone approved, until this specific vessel run has
        # actually finished — what "finished" means depends on which flow
        # this operation runs. Quantities are never a precondition either way.
        if operation.type == OperationType.vessel_only:
            if activity.complete_system_at is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Cannot submit a Vessel BDN — Complete Vessel Operation has not been recorded yet",
                )
        else:
            if activity.stage != VesselStage.discharge_completed:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Cannot submit a Vessel BDN — this vessel run has not reached discharge_completed yet "
                           f"(current stage: {activity.stage.value if activity.stage else 'not started'})",
                )

        # One active BDN per vessel run — mirrors Truck BDN's per-operation
        # uniqueness check, just scoped one level narrower.
        existing_result = await db.execute(
            select(BDN.id).where(
                and_(
                    BDN.vessel_activity_id == vessel_activity_id,
                    BDN.status.in_([BdnStatus.pending, BdnStatus.approved]),
                )
            )
        )
        if existing_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A Vessel BDN is already pending or approved for this vessel run",
            )

        # NOTE: this BDN is entirely about OUR vessel discharging OUT to the
        # receiving vessel named on the form. There used to be a "Truck vs
        # vessel reconciliation" block here (vessel_received_total_mt) — that
        # was the LOADING leg (trucks onto our own vessel), which belongs to
        # and is already independently reconciled on the BDNs tab
        # (bdn_service.py) with its own GOV/GSV and its own ROB credit.
        # Keeping it here too meant a single truck delivery could credit ROB
        # twice, and this form's actual subject (the outgoing discharge) had
        # no ROB effect of its own. Removed; see _apply_discharge_rob_debit.

        # Independently compute what the system has on record for THIS vessel
        # run — never used to fill or default anything the submitter enters.
        # System (not user-entered) timestamps are the comparison baseline
        # for vessel_only too — same spirit as the stage flow's snapshot.
        if operation.type == OperationType.vessel_only:
            system_product_type = None                                  # no product concept in this flow
            system_quantity_loaded = None                               # no "loaded" analogue either
            system_quantity_discharged = activity.discharged_quantity_litres
            system_commenced_at = activity.commence_system_at
            system_completed_at = activity.complete_system_at
        else:
            system_product_type = activity.product_type
            system_quantity_loaded = activity.vessel_received_mt
            system_quantity_discharged = activity.quantity_discharged_mt
            system_commenced_at = activity.stage_commence_discharge_at
            system_completed_at = activity.stage_discharge_completed_at

        bdn_number = await generate_bdn_number(db)

        bdn = BDN(
            bdn_number=bdn_number,
            operation_id=operation.id,
            vessel_id=activity.vessel_id,
            vessel_activity_id=vessel_activity_id,
            generated_by=current_user.id,
            status=BdnStatus.pending,
            # Legacy required columns — kept populated for backward compat.
            quantity_delivered_mt=data.quantity_discharged_litres,
            delivery_date=data.discharge_completed_at,
            company_name=data.company_name,
            product_type=data.product_type,
            discharge_location=data.discharge_location,
            receiving_vessel=data.receiving_vessel,
            quantity_loaded_litres=data.quantity_loaded_litres,
            quantity_discharged_litres=data.quantity_discharged_litres,
            variance_litres=data.quantity_loaded_litres - data.quantity_discharged_litres,
            density=data.density,
            temperature=data.temperature,
            vcf=data.vcf,
            discharge_gov=data.discharge_gov,
            discharge_gsv=data.discharge_gsv,
            discharge_mt_vacuum=data.discharge_mt_vacuum,
            discharge_commenced_at=data.discharge_commenced_at,
            discharge_completed_at=data.discharge_completed_at,
            discharge_completion_date=data.discharge_completion_date,
            received_gov=data.received_gov,
            received_gsv=data.received_gsv,
            received_mt_vacuum=data.received_mt_vacuum,
            system_product_type=system_product_type,
            system_quantity_loaded_litres=system_quantity_loaded,
            system_quantity_discharged_litres=system_quantity_discharged,
            system_discharge_commenced_at=system_commenced_at,
            system_discharge_completed_at=system_completed_at,
            notes=data.notes,
        )
        db.add(bdn)
        await db.flush()

        # Transition operation to bdn_pending (no-op if already there).
        if operation.status != OperationStatus.bdn_pending:
            await _transition_operation(operation, OperationStatus.bdn_pending, current_user, db, reason="Vessel BDN submitted")

        # Notify + email Bunker Manager (needs to approve) and Finance Manager (heads-up).
        recipients_result = await db.execute(
            select(User).where(User.role.in_([UserRole.bunker_manager, UserRole.finance_manager]))
        )
        for recipient in recipients_result.scalars().all():
            await notify(
                db=db, user_id=recipient.id, type_="bdn_ready",
                title="Vessel BDN Ready for Review",
                message=f"Vessel BDN {bdn_number} for operation {operation.operation_number} (activity {activity.activity_number}) is ready for review",
                priority="high" if recipient.role == UserRole.bunker_manager else "normal",
                operation_id=operation.id, action_url=f"/operations/{operation.id}",
                channels=["in_app", "whatsapp"], wa_template="bdn_submitted",
                wa_kwargs={"operation_number": operation.operation_number, "bdn_number": bdn_number, "quantity": str(data.quantity_discharged_litres)},
            )
            try:
                await email_vessel_bdn_submitted(
                    to_email=recipient.email, recipient_name=recipient.full_name,
                    operation_number=operation.operation_number, vessel_bdn_number=bdn_number,
                    quantity_loaded=str(data.quantity_loaded_litres), quantity_discharged=str(data.quantity_discharged_litres),
                )
            except Exception as exc:
                logger.warning("create_vessel_bdn: email failed for %s: %s", recipient.email, exc)

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation.id, action="CREATE_VESSEL_BDN",
            entity_type="vessel_bdn", entity_id=bdn.id,
            changes={
                "bdn_number": bdn_number, "vessel_activity_id": str(vessel_activity_id),
                "quantity_loaded_litres": str(data.quantity_loaded_litres),
                "quantity_discharged_litres": str(data.quantity_discharged_litres),
                "system_quantity_loaded_litres": str(system_quantity_loaded) if system_quantity_loaded is not None else None,
                "system_quantity_discharged_litres": str(system_quantity_discharged) if system_quantity_discharged is not None else None,
            },
        ))

        await db.flush()
        await db.refresh(bdn)
        bdn._generated_by_name = current_user.full_name
        return bdn

    @staticmethod
    async def create_vessel_bdn_for_leg(leg_id: UUID, data: VesselBdnCreate, current_user: User, db: AsyncSession) -> BDN:
        """One Vessel BDN per receiving-vessel leg — the six-stage +
        multiple-receiving-vessel-legs flow's analogue of create_vessel_bdn.
        Every field is still manually entered, nothing prefilled; the
        system's own comparison snapshot below is independently computed
        from the leg (and its parent activity's loading receipt)."""
        leg = await _get_vessel_activity_leg_or_404(leg_id, db)
        activity = await _get_vessel_activity_or_404(leg.vessel_activity_id, db)
        operation = await _get_operation_or_404(activity.operation_id, db)

        if leg.stage != VesselLegStage.discharge_completed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Cannot submit a Vessel BDN — this receiving vessel has not reached Discharge Completed yet "
                       f"(current stage: {leg.stage.value if leg.stage else 'not started'})",
            )

        # One active BDN per leg — scoped one level narrower than the
        # per-vessel-run uniqueness check above, since multiple legs now
        # legitimately share one vessel_activity_id.
        existing_result = await db.execute(
            select(BDN.id).where(
                and_(
                    BDN.vessel_leg_id == leg_id,
                    BDN.status.in_([BdnStatus.pending, BdnStatus.approved]),
                )
            )
        )
        if existing_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A Vessel BDN is already pending or approved for this receiving vessel",
            )

        # Independently computed system snapshot for the BM's comparison.
        # Received Quantity maps to the BDN's loaded-quantity analogue here
        # (it is not blank) — loading happens once on the parent activity,
        # discharge happens independently per leg.
        system_product_type = None
        system_quantity_loaded = activity.loading_received_quantity_litres
        system_quantity_discharged = leg.quantity_discharged_litres
        system_commenced_at = leg.stage_discharge_commenced_system_at
        system_completed_at = leg.stage_discharge_completed_system_at

        bdn_number = await generate_bdn_number(db)

        bdn = BDN(
            bdn_number=bdn_number,
            operation_id=operation.id,
            vessel_id=activity.vessel_id,
            vessel_activity_id=activity.id,
            vessel_leg_id=leg.id,
            generated_by=current_user.id,
            status=BdnStatus.pending,
            # Legacy required columns — kept populated for backward compat.
            quantity_delivered_mt=data.quantity_discharged_litres,
            delivery_date=data.discharge_completed_at,
            company_name=data.company_name,
            product_type=data.product_type,
            discharge_location=data.discharge_location,
            receiving_vessel=data.receiving_vessel,
            quantity_loaded_litres=data.quantity_loaded_litres,
            quantity_discharged_litres=data.quantity_discharged_litres,
            variance_litres=data.quantity_loaded_litres - data.quantity_discharged_litres,
            density=data.density,
            temperature=data.temperature,
            vcf=data.vcf,
            discharge_gov=data.discharge_gov,
            discharge_gsv=data.discharge_gsv,
            discharge_mt_vacuum=data.discharge_mt_vacuum,
            discharge_commenced_at=data.discharge_commenced_at,
            discharge_completed_at=data.discharge_completed_at,
            discharge_completion_date=data.discharge_completion_date,
            received_gov=data.received_gov,
            received_gsv=data.received_gsv,
            received_mt_vacuum=data.received_mt_vacuum,
            system_product_type=system_product_type,
            system_quantity_loaded_litres=system_quantity_loaded,
            system_quantity_discharged_litres=system_quantity_discharged,
            system_discharge_commenced_at=system_commenced_at,
            system_discharge_completed_at=system_completed_at,
            notes=data.notes,
        )
        db.add(bdn)
        await db.flush()

        if operation.status != OperationStatus.bdn_pending:
            await _transition_operation(operation, OperationStatus.bdn_pending, current_user, db, reason="Vessel BDN submitted")

        recipients_result = await db.execute(
            select(User).where(User.role.in_([UserRole.bunker_manager, UserRole.finance_manager]))
        )
        for recipient in recipients_result.scalars().all():
            await notify(
                db=db, user_id=recipient.id, type_="bdn_ready",
                title="Vessel BDN Ready for Review",
                message=f"Vessel BDN {bdn_number} for operation {operation.operation_number} "
                        f"(receiving vessel {leg.receiving_vessel_name}) is ready for review",
                priority="high" if recipient.role == UserRole.bunker_manager else "normal",
                operation_id=operation.id, action_url=f"/operations/{operation.id}",
                channels=["in_app", "whatsapp"], wa_template="bdn_submitted",
                wa_kwargs={"operation_number": operation.operation_number, "bdn_number": bdn_number, "quantity": str(data.quantity_discharged_litres)},
            )
            try:
                await email_vessel_bdn_submitted(
                    to_email=recipient.email, recipient_name=recipient.full_name,
                    operation_number=operation.operation_number, vessel_bdn_number=bdn_number,
                    quantity_loaded=str(data.quantity_loaded_litres), quantity_discharged=str(data.quantity_discharged_litres),
                )
            except Exception as exc:
                logger.warning("create_vessel_bdn_for_leg: email failed for %s: %s", recipient.email, exc)

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation.id, action="CREATE_VESSEL_BDN",
            entity_type="vessel_bdn", entity_id=bdn.id,
            changes={
                "bdn_number": bdn_number, "vessel_leg_id": str(leg_id),
                "quantity_loaded_litres": str(data.quantity_loaded_litres),
                "quantity_discharged_litres": str(data.quantity_discharged_litres),
                "system_quantity_loaded_litres": str(system_quantity_loaded) if system_quantity_loaded is not None else None,
                "system_quantity_discharged_litres": str(system_quantity_discharged) if system_quantity_discharged is not None else None,
            },
        ))

        await db.flush()
        await db.refresh(bdn)
        bdn._generated_by_name = current_user.full_name
        return bdn

    @staticmethod
    async def get_vessel_bdn(bdn_id: UUID, db: AsyncSession) -> BDN:
        result = await db.execute(
            select(BDN).where(BDN.id == bdn_id).options(selectinload(BDN.generator))
        )
        bdn = result.scalar_one_or_none()
        if not bdn:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vessel BDN not found")
        bdn._generated_by_name = bdn.generator.full_name if bdn.generator else None
        return bdn

    @staticmethod
    async def update_vessel_bdn(bdn_id: UUID, data: VesselBdnUpdate, current_user: User, db: AsyncSession) -> BDN:
        """Bunker Manager corrects any field — allowed regardless of status."""
        bdn = await VesselBdnService.get_vessel_bdn(bdn_id, db)
        operation = await _get_operation_or_404(bdn.operation_id, db)

        update_data = data.model_dump(exclude_unset=True, exclude={"reason"})
        if update_data.get("discharge_completed_at") and "discharge_completion_date" not in update_data:
            update_data["discharge_completion_date"] = update_data["discharge_completed_at"].date()

        # Keep variance_litres consistent with whichever quantity figures
        # the BM just corrected — it's derived, never independently edited.
        if "quantity_loaded_litres" in update_data or "quantity_discharged_litres" in update_data:
            new_loaded = update_data.get("quantity_loaded_litres", bdn.quantity_loaded_litres)
            new_discharged = update_data.get("quantity_discharged_litres", bdn.quantity_discharged_litres)
            update_data["variance_litres"] = new_loaded - new_discharged

        # An approved Full Operation BDN's discharge_mt_vacuum already debited
        # the vessel's ROB — reverse that exact debit before applying the
        # correction, then reapply with the corrected figure, so the ledger
        # never double-counts or goes stale. Gated to full_operation because
        # that's the only type approve_vessel_bdn ever touches ROB for —
        # vessel_only's ROB is written by record_leg_quantities instead, and
        # discharge_mt_vacuum is populated there too, so this must not fire.
        rob_needs_reapply = (
            operation.type == OperationType.full_operation
            and bdn.status == BdnStatus.approved
            and "discharge_mt_vacuum" in update_data
            and update_data["discharge_mt_vacuum"] != bdn.discharge_mt_vacuum
        )
        if rob_needs_reapply:
            await VesselBdnService._reverse_rob_entry(bdn, db)

        changes = capture_diff(bdn, update_data)

        if rob_needs_reapply:
            await VesselBdnService._apply_discharge_rob_debit(bdn, current_user, db)
        db.add(AuditLog(
            user_id=current_user.id, operation_id=bdn.operation_id, action="UPDATE_VESSEL_BDN",
            entity_type="vessel_bdn", entity_id=bdn.id, changes=changes, reason=data.reason,
        ))
        await db.flush()
        await db.refresh(bdn)
        return bdn

    @staticmethod
    async def _approval_progress(operation_id: UUID, db: AsyncSession) -> tuple[int, int]:
        """(total runs, runs with an approved BDN) — cancelled runs don't
        count toward the total. Vessel-only operations count receiving-
        vessel legs (each leg submits its own BDN); everything else counts
        VesselActivity rows exactly as before."""
        operation = await db.get(Operation, operation_id)
        if operation and operation.type == OperationType.vessel_only:
            total_result = await db.execute(
                select(func.count()).select_from(VesselActivityLeg).join(
                    VesselActivity, VesselActivityLeg.vessel_activity_id == VesselActivity.id
                ).where(
                    and_(
                        VesselActivity.operation_id == operation_id,
                        VesselActivity.status != VesselActivityStatus.cancelled,
                        VesselActivityLeg.cancelled_at.is_(None),
                    )
                )
            )
            total = total_result.scalar() or 0

            approved_result = await db.execute(
                select(func.count(func.distinct(BDN.vessel_leg_id))).select_from(BDN).join(
                    VesselActivityLeg, BDN.vessel_leg_id == VesselActivityLeg.id
                ).join(
                    VesselActivity, VesselActivityLeg.vessel_activity_id == VesselActivity.id
                ).where(
                    and_(
                        VesselActivity.operation_id == operation_id,
                        VesselActivity.status != VesselActivityStatus.cancelled,
                        VesselActivityLeg.cancelled_at.is_(None),
                        BDN.status == BdnStatus.approved,
                    )
                )
            )
            approved = approved_result.scalar() or 0
            return total, approved

        total_result = await db.execute(
            select(func.count()).select_from(VesselActivity).where(
                and_(VesselActivity.operation_id == operation_id, VesselActivity.status != VesselActivityStatus.cancelled)
            )
        )
        total = total_result.scalar() or 0

        approved_result = await db.execute(
            select(func.count(func.distinct(BDN.vessel_activity_id))).select_from(BDN).join(
                VesselActivity, BDN.vessel_activity_id == VesselActivity.id
            ).where(
                and_(
                    VesselActivity.operation_id == operation_id,
                    VesselActivity.status != VesselActivityStatus.cancelled,
                    BDN.status == BdnStatus.approved,
                )
            )
        )
        approved = approved_result.scalar() or 0
        return total, approved

    @staticmethod
    def _rob_source_description(bdn: BDN) -> str:
        return f"Vessel BDN {bdn.bdn_number}"

    @staticmethod
    async def _apply_discharge_rob_debit(bdn: BDN, current_user: User, db: AsyncSession) -> None:
        """This BDN records OUR vessel discharging discharge_mt_vacuum OUT to
        the receiving vessel — approving it lowers our vessel's ROB by that
        amount. Same entry_type/negative-quantity convention already used for
        an outgoing discharge in vessel_activity_service.py and
        vessel_discharge_service.py; this is the Vessel BDN flow's analogue.
        Assumes no prior entry for this BDN exists yet — call
        _reverse_rob_entry first if one might."""
        vessel = await db.get(Vessel, bdn.vessel_id)
        if not vessel:
            return
        rob_before = vessel.current_rob_mt or 0
        rob_after = rob_before - bdn.discharge_mt_vacuum
        vessel.current_rob_mt = rob_after
        db.add(RobEntry(
            vessel_id=bdn.vessel_id,
            operation_id=bdn.operation_id,
            entry_type=RobEntryType.discharge,
            quantity_mt=-bdn.discharge_mt_vacuum,
            rob_before_mt=rob_before,
            rob_after_mt=rob_after,
            recorded_by=current_user.id,
            source_description=VesselBdnService._rob_source_description(bdn),
            notes=f"Vessel BDN {bdn.bdn_number} approved — discharged to {bdn.receiving_vessel or 'receiving vessel'}",
        ))
        await db.flush()

    @staticmethod
    async def _reverse_rob_entry(bdn: BDN, db: AsyncSession) -> None:
        """Delete-and-reinsert reversal, same pattern already used three
        times in vessel_activity_service.py for BM corrections — finds this
        BDN's prior RobEntry rows by their exact source_description marker,
        subtracts their net effect off the vessel's current ROB, deletes
        them. Caller writes the fresh entry afterward."""
        vessel = await db.get(Vessel, bdn.vessel_id)
        if not vessel:
            return
        prior_result = await db.execute(
            select(RobEntry).where(
                and_(RobEntry.vessel_id == bdn.vessel_id, RobEntry.source_description == VesselBdnService._rob_source_description(bdn))
            )
        )
        prior_entries = list(prior_result.scalars().all())
        if not prior_entries:
            return
        reversed_net = sum((e.quantity_mt for e in prior_entries), type(prior_entries[0].quantity_mt)(0))
        vessel.current_rob_mt = (vessel.current_rob_mt or 0) - reversed_net
        for entry in prior_entries:
            await db.delete(entry)
        await db.flush()

    @staticmethod
    async def approve_vessel_bdn(bdn_id: UUID, current_user: User, db: AsyncSession) -> tuple[BDN, int, int, bool]:
        """Approves this one BDN, then checks whether EVERY vessel run on the
        operation now has an approved BDN — only then does the operation
        transition to bdn_approved. Never trust the UI alone for this gate.
        Returns (bdn, total_runs, approved_runs, operation_completed_gate_cleared)."""
        bdn = await VesselBdnService.get_vessel_bdn(bdn_id, db)

        if bdn.status != BdnStatus.pending:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Cannot approve Vessel BDN with status '{bdn.status.value}'")

        bdn.status = BdnStatus.approved
        bdn.reviewed_by = current_user.id
        bdn.approved_at = datetime.utcnow()
        await db.flush()

        operation = await _get_operation_or_404(bdn.operation_id, db)

        # Full Operation only: approving this BDN is what updates the
        # vessel's ROB now — replaces what the retired Complete step used to
        # do. Runs per-approval, independent of whether every vessel run on
        # the operation is approved yet.
        if operation.type == OperationType.full_operation:
            await VesselBdnService._apply_discharge_rob_debit(bdn, current_user, db)

        total, approved = await VesselBdnService._approval_progress(operation.id, db)
        gate_cleared = total > 0 and approved >= total

        if gate_cleared and operation.status != OperationStatus.bdn_approved:
            await _transition_operation(operation, OperationStatus.bdn_approved, current_user, db, reason="All vessel run BDNs approved")

        fm_result = await db.execute(select(User).where(User.role == UserRole.finance_manager))
        for fm in fm_result.scalars().all():
            await notify(
                db=db, user_id=fm.id, type_="approved",
                title="Vessel BDN Approved" + (" — Invoice Can Be Generated" if gate_cleared else ""),
                message=f"Vessel BDN {bdn.bdn_number} for operation {operation.operation_number} has been approved."
                        + (" All vessel runs are now approved — invoice can be generated." if gate_cleared else f" {approved} of {total} vessel run(s) approved so far."),
                priority="normal", operation_id=operation.id, action_url=f"/operations/{operation.id}",
            )

        await notify(
            db=db, user_id=bdn.generated_by, type_="approved",
            title="Your Vessel BDN Has Been Approved",
            message=f"Vessel BDN {bdn.bdn_number} has been approved by the bunker manager",
            priority="normal", operation_id=operation.id, action_url=f"/operations/{operation.id}",
            channels=["in_app", "whatsapp"], wa_template="bdn_approved",
            wa_kwargs={"operation_number": operation.operation_number, "bdn_number": bdn.bdn_number},
        )

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation.id, action="APPROVE_VESSEL_BDN",
            entity_type="vessel_bdn", entity_id=bdn.id,
            changes={"status": {"from": "pending", "to": "approved"}, "approved_runs": f"{approved}/{total}"},
        ))

        await db.flush()
        await db.refresh(bdn)
        return bdn, total, approved, gate_cleared

    @staticmethod
    async def reject_vessel_bdn(bdn_id: UUID, reason: str, current_user: User, db: AsyncSession) -> BDN:
        if not reason or len(reason.strip()) < 10:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Rejection reason must be at least 10 characters")

        bdn = await VesselBdnService.get_vessel_bdn(bdn_id, db)
        if bdn.status != BdnStatus.pending:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Cannot reject Vessel BDN with status '{bdn.status.value}'")

        bdn.status = BdnStatus.rejected
        bdn.reviewed_by = current_user.id
        bdn.rejection_reason = reason.strip()

        operation = await _get_operation_or_404(bdn.operation_id, db)
        await _transition_operation(operation, OperationStatus.vessel_operations, current_user, db, reason=f"Vessel BDN rejected: {reason}")

        await notify(
            db=db, user_id=bdn.generated_by, type_="rejected",
            title="Vessel BDN Rejected",
            message=f"Vessel BDN {bdn.bdn_number} has been rejected. Reason: {reason}",
            priority="high", operation_id=operation.id, action_url=f"/operations/{operation.id}",
            channels=["in_app", "whatsapp"], wa_template="bdn_rejected",
            wa_kwargs={"operation_number": operation.operation_number, "bdn_number": bdn.bdn_number, "reason": reason},
        )

        db.add(AuditLog(
            user_id=current_user.id, operation_id=operation.id, action="REJECT_VESSEL_BDN",
            entity_type="vessel_bdn", entity_id=bdn.id,
            changes={"status": {"from": "pending", "to": "rejected"}, "reason": reason},
        ))

        await db.flush()
        await db.refresh(bdn)
        return bdn

    @staticmethod
    async def delete_vessel_bdn(bdn_id: UUID, current_user: User, db: AsyncSession) -> None:
        """Bunker Manager deletes a Vessel BDN outright. If it was approved
        (full_operation only — that's the only case that ever debited ROB),
        the debit is reversed first so deleting it never leaves the vessel's
        ROB permanently short."""
        bdn = await VesselBdnService.get_vessel_bdn(bdn_id, db)
        operation = await _get_operation_or_404(bdn.operation_id, db)

        if bdn.status == BdnStatus.approved and operation.type == OperationType.full_operation:
            await VesselBdnService._reverse_rob_entry(bdn, db)

        db.add(AuditLog(
            user_id=current_user.id, operation_id=bdn.operation_id, action="DELETE_VESSEL_BDN",
            entity_type="vessel_bdn", entity_id=bdn.id,
            changes={"bdn_number": bdn.bdn_number, "status_at_deletion": bdn.status.value,
                     "discharge_mt_vacuum": str(bdn.discharge_mt_vacuum)},
        ))
        await db.execute(delete(AuditLog).where(
            AuditLog.entity_type == "vessel_bdn", AuditLog.entity_id == bdn.id, AuditLog.action != "DELETE_VESSEL_BDN"
        ))
        await db.delete(bdn)
        await db.flush()
