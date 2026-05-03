from typing import Dict, List, Optional
from pydantic import BaseModel
from decimal import Decimal


class StatusCount(BaseModel):
    status: str
    count: int


class OperationsOverview(BaseModel):
    total_operations: int
    by_status: List[StatusCount]
    total_volume_mt: Optional[Decimal] = None
    total_pfis: int
    total_bdns_approved: int
    active_operations: int
    completed_this_month: int


class TruckStats(BaseModel):
    total_trucks: int
    available: int
    in_transit: int
    discharging: int
    total_operations: int
    total_volume_mt: Optional[Decimal] = None


class VesselStats(BaseModel):
    total_vessels: int
    total_rob_entries: int
    current_rob_mt: Optional[Decimal] = None


class RevenueItem(BaseModel):
    currency: str
    total_amount: Decimal
    payment_count: int


class AnalyticsDashboard(BaseModel):
    operations: OperationsOverview
    trucks: TruckStats
    vessels: VesselStats
    revenue: List[RevenueItem]
