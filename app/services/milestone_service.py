"""
Client milestone service.
Milestones are auto-created on key operation status transitions so that
clients can track progress in their portal without seeing internal details.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.audit import ClientMilestone
from app.models.enums import OperationStatus

# Status → (milestone_type, client-visible title, description)
_MILESTONE_MAP: dict = {
    OperationStatus.tasks_assigned: (
        "team_assigned",
        "Operations Team Assigned",
        "Your operation has been assigned to our logistics and finance team.",
    ),
    OperationStatus.feedback_approved: (
        "logistics_confirmed",
        "Logistics Confirmed",
        "Truck readiness has been reviewed and approved.",
    ),
    OperationStatus.pfi_linked: (
        "invoice_issued",
        "Pro-Forma Invoice Issued",
        "Your Pro-Forma Invoice is ready. Please arrange payment.",
    ),
    OperationStatus.payment_confirmed: (
        "payment_received",
        "Payment Confirmed",
        "Your payment has been verified. Vessel operations will now proceed.",
    ),
    OperationStatus.vessel_operations: (
        "vessel_operations_started",
        "Vessel Operations Started",
        "Bunker delivery operations have commenced on the vessel.",
    ),
    OperationStatus.bdn_approved: (
        "bdn_issued",
        "Bunker Delivery Note Approved",
        "Your Bunker Delivery Note has been verified and approved.",
    ),
    OperationStatus.completed: (
        "completed",
        "Operation Completed",
        "Your operation has been successfully completed.",
    ),
}


async def create_milestone_if_applicable(
    db: AsyncSession,
    operation_id: UUID,
    to_status: OperationStatus,
) -> None:
    """Create a ClientMilestone row when the new status warrants one."""
    entry = _MILESTONE_MAP.get(to_status)
    if not entry:
        return
    milestone_type, title, description = entry
    milestone = ClientMilestone(
        operation_id=operation_id,
        milestone_type=milestone_type,
        title=title,
        description=description,
        reached_at=datetime.utcnow(),
        is_visible=True,
    )
    db.add(milestone)


async def list_milestones(db: AsyncSession, operation_id: UUID) -> list:
    """Return all visible milestones for an operation, oldest first."""
    result = await db.execute(
        select(ClientMilestone)
        .where(
            ClientMilestone.operation_id == operation_id,
            ClientMilestone.is_visible == True,
        )
        .order_by(ClientMilestone.reached_at.asc())
    )
    return result.scalars().all()
