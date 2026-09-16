"""Response shapes for the Command Center.

Phase 3 owns this file. Phase 1's `schemas.py` is frozen and Phase 2 has its
own — see §7b of KPI-WORKLOG.md.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AttentionItem(BaseModel):
    """One thing waiting on the Bunker Manager.

    `severity` drives colour, `age_hours` drives sort order. Both are computed
    server-side so every client agrees on what is urgent.
    """
    kind: str                       # bdn_pending | feedback_pending | stalled | truck_loss | safety_audit | licence_runway | unsent_email
    severity: str                   # critical | warning | info
    title: str
    subtitle: Optional[str] = None
    operation_id: Optional[UUID] = None
    operation_number: Optional[str] = None
    since: Optional[datetime] = None
    age_hours: Optional[float] = None


class LiveOperation(BaseModel):
    operation_id: UUID
    operation_number: str
    operation_type: Optional[str] = None
    status: Optional[str] = None
    idle_hours: Optional[float] = None
    last_event_at: Optional[datetime] = None


class ProductVolume(BaseModel):
    product_type: str
    mt_vacuum: Optional[float] = None


class VolumePulse(BaseModel):
    """Vessel side and truck side are kept apart and never added together.

    The vessel figures come from the BDN's `*_mt_vacuum` columns. In
    production those hold litres-scale values, not tonnes (see
    `vessel_figures_suspect`), so the UI must not label them MT(vac) while
    that flag is set.
    """
    mt_delivered_this_month: Optional[float] = None
    mt_delivered_last_month: Optional[float] = None
    vessel_figures_suspect: bool = False
    vessel_unit_label: str = "as recorded"
    litres_trucked_this_month: Optional[float] = None
    litres_trucked_last_month: Optional[float] = None
    by_product: List[ProductVolume] = Field(default_factory=list)


class WorstOperation(BaseModel):
    operation_id: UUID
    operation_number: str
    loss_pct: float
    litres_lost: Optional[float] = None


class VendorLeagueRow(BaseModel):
    vendor_name: str
    trips: int
    trucks: int
    avg_litres_lost: Optional[float] = None
    over_cap_trips: int = 0
    over_cap_pct: Optional[float] = None


class LossWatch(BaseModel):
    truck_loss_pct_this_month: Optional[float] = None
    avg_litres_lost_per_truck: Optional[float] = None
    loss_cap_litres: Optional[float] = None       # the live target, after overrides
    trucks_over_cap: int = 0
    trucks_measured: int = 0
    worst_operations: List[WorstOperation] = Field(default_factory=list)

    # ── What the loss is worth, in money ──
    # An estimate, and labelled as one. `price_basis` says exactly how the
    # rate was arrived at so nobody mistakes it for an invoiced figure.
    litres_lost_this_month: Optional[float] = None
    naira_lost_estimate: Optional[float] = None
    price_per_litre: Optional[float] = None
    price_basis: Optional[str] = None


class FigureDiscrepancy(BaseModel):
    """One document whose submitted figures disagree with what the system had
    already recorded. The Data Trust Score in its raw, per-document form."""
    bdn_number: str
    operation_id: Optional[UUID] = None
    operation_number: Optional[str] = None
    submitted_by: Optional[str] = None
    field: str                      # "discharged" | "loaded"
    submitted: Optional[float] = None
    system_recorded: Optional[float] = None
    litres_gap: Optional[float] = None
    gap_pct: Optional[float] = None
    naira_gap: Optional[float] = None   # only when a usable price exists


class RolePulse(BaseModel):
    role: str
    role_label: str
    score: Optional[float] = None
    rating: Optional[str] = None


class TeamPulse(BaseModel):
    """Reads Phase 2's kpi_snapshots. Until that exists this reports
    unavailable rather than erroring or showing zeros — a missing feature must
    not look like a team scoring nothing."""
    available: bool = False
    unavailable_reason: Optional[str] = None
    period: Optional[str] = None
    roles: List[RolePulse] = Field(default_factory=list)


class FleetLeagueRow(BaseModel):
    truck_id: UUID
    truck_number: str
    trips: int
    avg_litres_lost: Optional[float] = None
    avg_discharge_hours: Optional[float] = None
    over_cap_trips: int = 0


class LicenceRunway(BaseModel):
    product_type: str
    remaining_litres: Optional[float] = None
    avg_monthly_litres: Optional[float] = None
    days_left: Optional[float] = None
    severity: Optional[str] = None   # critical | warning | ok


class MoneyStrip(BaseModel):
    currency: str = "NGN"
    collected_this_month: Optional[float] = None
    outstanding_invoices: Optional[float] = None
    invoices_outstanding_count: int = 0
    vouchers_pending: int = 0


class CommandCenterOut(BaseModel):
    generated_at: datetime
    attention: List[AttentionItem] = Field(default_factory=list)
    attention_total: int = 0
    live_operations: List[LiveOperation] = Field(default_factory=list)
    volume: VolumePulse
    loss: LossWatch
    team: TeamPulse
    fleet: List[FleetLeagueRow] = Field(default_factory=list)
    vendors: List[VendorLeagueRow] = Field(default_factory=list)
    discrepancies: List[FigureDiscrepancy] = Field(default_factory=list)
    licences: List[LicenceRunway] = Field(default_factory=list)
    money: MoneyStrip
    # Panels that could not be computed, so the UI can say so rather than
    # rendering an empty card that looks like good news.
    degraded: List[str] = Field(default_factory=list)
    # Figures that computed successfully but should not be trusted at face
    # value — a wrong number presented confidently is worse than no number.
    data_warnings: List[str] = Field(default_factory=list)
