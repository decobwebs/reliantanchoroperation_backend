from datetime import datetime, date
from typing import List
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, extract

from app.models.operation import Operation
from app.models.truck import Truck, TruckOperation
from app.models.vessel import Vessel
from app.models.bdn import RobEntry, BDN
from app.models.finance import PFI, Payment
from app.models.enums import OperationStatus, TruckStatus, BdnStatus
from app.schemas.analytics import (
    AnalyticsDashboard, OperationsOverview, StatusCount,
    TruckStats, VesselStats, RevenueItem,
)


class AnalyticsService:

    @staticmethod
    async def get_dashboard(db: AsyncSession) -> AnalyticsDashboard:
        now = datetime.utcnow()
        month_start = datetime(now.year, now.month, 1)

        # ── Operations ──────────────────────────────────────────────────────────

        # Count by status (non-deleted)
        status_stmt = (
            select(Operation.status, func.count().label("cnt"))
            .where(Operation.deleted_at.is_(None))
            .group_by(Operation.status)
        )
        status_result = await db.execute(status_stmt)
        by_status = [
            StatusCount(status=row.status.value, count=row.cnt)
            for row in status_result.all()
        ]

        total_ops = sum(s.count for s in by_status)

        active_statuses = {
            OperationStatus.tasks_assigned, OperationStatus.awaiting_feedback,
            OperationStatus.feedback_submitted, OperationStatus.feedback_approved,
            OperationStatus.pfi_linked, OperationStatus.payment_processing,
            OperationStatus.payment_confirmed, OperationStatus.vessel_operations,
            OperationStatus.bdn_pending, OperationStatus.bdn_approved,
        }
        active_ops = sum(
            s.count for s in by_status
            if OperationStatus(s.status) in active_statuses
        )

        # Completed this month
        completed_stmt = (
            select(func.count())
            .select_from(Operation)
            .where(
                and_(
                    Operation.status == OperationStatus.completed,
                    Operation.completed_at >= month_start,
                    Operation.deleted_at.is_(None),
                )
            )
        )
        completed_month = (await db.execute(completed_stmt)).scalar_one()

        # Total volume (actual_volume_mt)
        vol_stmt = select(func.sum(Operation.actual_volume_mt)).where(
            Operation.deleted_at.is_(None)
        )
        total_volume = (await db.execute(vol_stmt)).scalar_one()

        # PFI count
        pfi_count = (await db.execute(select(func.count()).select_from(PFI))).scalar_one()

        # Approved BDNs
        bdn_count = (
            await db.execute(
                select(func.count()).select_from(BDN).where(BDN.status == BdnStatus.approved)
            )
        ).scalar_one()

        ops_overview = OperationsOverview(
            total_operations=total_ops,
            by_status=by_status,
            total_volume_mt=total_volume,
            total_pfis=pfi_count,
            total_bdns_approved=bdn_count,
            active_operations=active_ops,
            completed_this_month=completed_month,
        )

        # ── Trucks ──────────────────────────────────────────────────────────────

        truck_total = (await db.execute(select(func.count()).select_from(Truck))).scalar_one()
        truck_avail = (
            await db.execute(
                select(func.count()).select_from(Truck).where(Truck.status == TruckStatus.available)
            )
        ).scalar_one()
        truck_transit = (
            await db.execute(
                select(func.count()).select_from(Truck).where(Truck.status == TruckStatus.in_transit)
            )
        ).scalar_one()
        truck_disch = (
            await db.execute(
                select(func.count()).select_from(Truck).where(Truck.status == TruckStatus.discharging)
            )
        ).scalar_one()
        truck_ops_count = (
            await db.execute(select(func.count()).select_from(TruckOperation))
        ).scalar_one()
        truck_vol = (
            await db.execute(select(func.sum(TruckOperation.quantity_discharged_mt)))
        ).scalar_one()

        trucks = TruckStats(
            total_trucks=truck_total,
            available=truck_avail,
            in_transit=truck_transit,
            discharging=truck_disch,
            total_operations=truck_ops_count,
            total_volume_mt=truck_vol,
        )

        # ── Vessels ─────────────────────────────────────────────────────────────

        vessel_total = (await db.execute(select(func.count()).select_from(Vessel))).scalar_one()
        rob_entries = (await db.execute(select(func.count()).select_from(RobEntry))).scalar_one()

        # Approximate current ROB = sum of all positive - sum of all negative entries
        rob_sum_stmt = select(func.sum(RobEntry.quantity_mt))
        rob_current = (await db.execute(rob_sum_stmt)).scalar_one()

        vessels = VesselStats(
            total_vessels=vessel_total,
            total_rob_entries=rob_entries,
            current_rob_mt=rob_current,
        )

        # ── Revenue ─────────────────────────────────────────────────────────────

        rev_stmt = (
            select(
                Payment.currency,
                func.sum(Payment.amount).label("total"),
                func.count().label("cnt"),
            )
            .group_by(Payment.currency)
            .order_by(func.sum(Payment.amount).desc())
        )
        rev_result = await db.execute(rev_stmt)
        revenue = [
            RevenueItem(
                currency=row.currency,
                total_amount=row.total or Decimal("0"),
                payment_count=row.cnt,
            )
            for row in rev_result.all()
        ]

        return AnalyticsDashboard(
            operations=ops_overview,
            trucks=trucks,
            vessels=vessels,
            revenue=revenue,
        )

    @staticmethod
    async def get_operations_summary(
        db: AsyncSession,
        year: int,
    ) -> List[dict]:
        """Monthly operation counts for the given year."""
        stmt = (
            select(
                extract("month", Operation.created_at).label("month"),
                func.count().label("total"),
                func.sum(
                    func.cast(Operation.status == OperationStatus.completed, Integer if False else func.count().type.__class__)
                ).label("completed"),
            )
            .where(
                and_(
                    extract("year", Operation.created_at) == year,
                    Operation.deleted_at.is_(None),
                )
            )
            .group_by(extract("month", Operation.created_at))
            .order_by(extract("month", Operation.created_at))
        )
        # Simpler approach: just count totals per month
        stmt = (
            select(
                extract("month", Operation.created_at).label("month"),
                func.count().label("total"),
            )
            .where(
                and_(
                    extract("year", Operation.created_at) == year,
                    Operation.deleted_at.is_(None),
                )
            )
            .group_by(extract("month", Operation.created_at))
            .order_by(extract("month", Operation.created_at))
        )
        result = await db.execute(stmt)
        month_names = [
            "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
        ]
        return [
            {"month": month_names[int(row.month) - 1], "total": row.total}
            for row in result.all()
        ]
