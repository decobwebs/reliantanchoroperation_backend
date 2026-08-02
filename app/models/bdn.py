import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, DateTime, Date, Text, Numeric, ForeignKey, Integer,
    Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from app.database import Base
from app.models.enums import RobEntryType, BdnStatus, VesselActivityStatus, VesselStage, VesselLegStage, AuditResult


class RobEntry(Base):
    """Immutable ledger of every ROB change on a vessel. Never updated or deleted."""
    __tablename__ = "rob_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=True)
    entry_type = Column(SAEnum(RobEntryType, name="rob_entry_type"), nullable=False)
    quantity_mt = Column(Numeric(12, 3), nullable=False)   # signed; discharge is negative
    rob_before_mt = Column(Numeric(12, 3), nullable=False)
    rob_after_mt = Column(Numeric(12, 3), nullable=False)
    recorded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # Source traceability
    truck_operation_id = Column(UUID(as_uuid=True), ForeignKey("truck_operations.id"), nullable=True)
    source_vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=True)
    supervisor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Physical measurements
    spillage_mt = Column(Numeric(12, 3), nullable=True)
    temperature_celsius = Column(Numeric(6, 2), nullable=True)

    source_description = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    vessel = relationship("Vessel", foreign_keys=[vessel_id], back_populates="rob_entries")
    source_vessel = relationship("Vessel", foreign_keys=[source_vessel_id])
    operation = relationship("Operation", back_populates="rob_entries")
    recorder = relationship("User", foreign_keys=[recorded_by])
    supervisor = relationship("User", foreign_keys=[supervisor_id])
    truck_operation = relationship("TruckOperation", foreign_keys=[truck_operation_id], back_populates="rob_entries")


class BDN(Base):
    """Vessel BDN — one per vessel run (vessel_activity_id), mirroring
    TruckBdn's manual-required-fields + system-comparison pattern. Old
    columns (quantity_delivered_mt, density, temperature, product_type,
    delivery_date) stay for backward compat with any pre-existing rows;
    new writes go through the richer fields below instead."""
    __tablename__ = "bdns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bdn_number = Column(String(20), unique=True, nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=False)
    vessel_activity_id = Column(UUID(as_uuid=True), ForeignKey("vessel_activities.id"), nullable=True)
    # Vessel-only, one-BDN-per-receiving-vessel flow — set alongside
    # vessel_activity_id (which still points at the parent loading run) so
    # existing operation_id-scoped BDN queries need no change.
    vessel_leg_id = Column(UUID(as_uuid=True), ForeignKey("vessel_activity_legs.id"), nullable=True)
    generated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    reviewed_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    status = Column(SAEnum(BdnStatus, name="bdn_status"), default=BdnStatus.pending, nullable=False)
    quantity_delivered_mt = Column(Numeric(12, 3), nullable=False)
    product_type = Column(String(100), nullable=True)
    density = Column(Numeric(8, 4), nullable=True)
    temperature = Column(Numeric(6, 2), nullable=True)
    delivery_date = Column(DateTime(timezone=True), nullable=False)
    rejection_reason = Column(Text, nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    pdf_url = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    version = Column(Integer, default=1, nullable=False)

    # ── Manually entered, required fields (new-flow submissions) ──
    company_name = Column(String(200), nullable=True)
    discharge_location = Column(Text, nullable=True)
    receiving_vessel = Column(String(200), nullable=True)  # the client's ship, e.g. "MV Breydel" — free text
    quantity_loaded_litres = Column(Numeric(14, 2), nullable=True)
    quantity_discharged_litres = Column(Numeric(14, 2), nullable=True)
    variance_litres = Column(Numeric(14, 2), nullable=True)
    temperature_before_loading = Column(Numeric(6, 2), nullable=True)
    temperature_after_loading = Column(Numeric(6, 2), nullable=True)
    vcf = Column(Numeric(8, 4), nullable=True)
    # Discharging vessel's own readings — renamed from gov/gsv/mt_vacuum
    # (migration 047) for symmetry with the received_* block below.
    discharge_gov = Column(Numeric(14, 2), nullable=True)
    discharge_gsv = Column(Numeric(14, 2), nullable=True)
    discharge_mt_vacuum = Column(Numeric(12, 3), nullable=True)
    discharge_commenced_at = Column(DateTime(timezone=True), nullable=True)
    discharge_completed_at = Column(DateTime(timezone=True), nullable=True)
    discharge_completion_date = Column(Date, nullable=True)

    # ── Receiving vessel's own independent readings (migration 047) —
    # never derived from the discharge_* block above. Optional: new data
    # that in-flight operations at rollout won't have.
    received_gov = Column(Numeric(14, 2), nullable=True)
    received_gsv = Column(Numeric(14, 2), nullable=True)
    received_mt_vacuum = Column(Numeric(12, 3), nullable=True)

    # ── System-computed snapshot at submission — comparison only ──
    system_product_type = Column(String(100), nullable=True)
    system_discharge_location = Column(Text, nullable=True)
    system_quantity_loaded_litres = Column(Numeric(14, 2), nullable=True)
    system_quantity_discharged_litres = Column(Numeric(14, 2), nullable=True)
    system_discharge_commenced_at = Column(DateTime(timezone=True), nullable=True)
    system_discharge_completed_at = Column(DateTime(timezone=True), nullable=True)

    operation = relationship("Operation", back_populates="bdns")
    vessel = relationship("Vessel", back_populates="bdns")
    vessel_activity = relationship("VesselActivity", foreign_keys=[vessel_activity_id])
    vessel_leg = relationship("VesselActivityLeg", foreign_keys=[vessel_leg_id])
    generator = relationship("User", foreign_keys=[generated_by])
    reviewer = relationship("User", foreign_keys=[reviewed_by])
    invoices = relationship("Invoice", back_populates="bdn")


class VesselDischargeEvent(Base):
    """Records a vessel-to-vessel or vessel-to-client discharge event."""
    __tablename__ = "vessel_discharge_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    source_vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=False)
    destination_vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=True)

    product_type = Column(String(50), nullable=True)
    quantity_mt = Column(Numeric(12, 3), nullable=False)
    spillage_mt = Column(Numeric(12, 3), nullable=True)
    temperature_celsius = Column(Numeric(6, 2), nullable=True)
    density = Column(Numeric(8, 4), nullable=True)

    discharge_start_at = Column(DateTime(timezone=True), nullable=True)
    discharge_end_at = Column(DateTime(timezone=True), nullable=True)

    supervisor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    rob_entry_id = Column(UUID(as_uuid=True), ForeignKey("rob_entries.id"), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    operation = relationship("Operation", back_populates="vessel_discharge_events")
    source_vessel = relationship("Vessel", foreign_keys=[source_vessel_id], back_populates="discharge_events_as_source")
    destination_vessel = relationship("Vessel", foreign_keys=[destination_vessel_id], back_populates="discharge_events_as_dest")
    supervisor = relationship("User", foreign_keys=[supervisor_id])
    rob_entry = relationship("RobEntry", foreign_keys=[rob_entry_id])


class TerminalLoadingReceipt(Base):
    """A terminal-sourced loading intake reading (VesselSourceType.terminal
    operations only) — quantity + GOV/GSV/MT, no density/temperature. Lean
    counterpart to the truck flow's per-truck delivery figures; both feed
    the same operation-level Total Loaded Quantity aggregate."""
    __tablename__ = "terminal_loading_receipts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    quantity_litres = Column(Numeric(14, 2), nullable=False)
    gov = Column(Numeric(14, 2), nullable=True)
    gsv = Column(Numeric(14, 2), nullable=True)
    mt_vacuum = Column(Numeric(12, 3), nullable=True)
    description = Column(Text, nullable=True)
    recorded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    recorded_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    operation = relationship("Operation", back_populates="terminal_loading_receipts")
    recorder = relationship("User", foreign_keys=[recorded_by])


class VesselActivity(Base):
    """Marine Supervisor oversight session — tracks vessel bunkering/discharge quantities and ROB reconciliation."""
    __tablename__ = "vessel_activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    activity_number = Column(String(20), unique=True, nullable=False)
    operation_id = Column(UUID(as_uuid=True), ForeignKey("operations.id"), nullable=False)
    vessel_id = Column(UUID(as_uuid=True), ForeignKey("vessels.id"), nullable=False)
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    assigned_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # Quantity tracking — Full Operation flow
    truck_delivered_mt = Column(Numeric(12, 3), nullable=True)      # total from truck operations
    vessel_received_mt = Column(Numeric(12, 3), nullable=True)      # measured at vessel
    variance_mt = Column(Numeric(12, 3), nullable=True)             # truck_delivered - vessel_received

    # ROB reconciliation
    initial_rob_mt = Column(Numeric(12, 3), nullable=True)           # BM-set vessel ROB at activity creation
    previous_rob_mt = Column(Numeric(12, 3), nullable=True)         # ROB before bunkering starts
    new_rob_mt = Column(Numeric(12, 3), nullable=True)              # previous_rob + vessel_received
    quantity_discharged_mt = Column(Numeric(12, 3), nullable=True)  # discharge to another vessel (optional)
    final_rob_mt = Column(Numeric(12, 3), nullable=True)            # new_rob - quantity_discharged

    # Physical measurements
    product_type = Column(String(50), nullable=True)
    temperature_celsius = Column(Numeric(6, 2), nullable=True)
    density = Column(Numeric(8, 4), nullable=True)
    spillage_mt = Column(Numeric(12, 3), nullable=True)

    # Bunkering / discharge timing
    bunkering_start_at = Column(DateTime(timezone=True), nullable=True)
    bunkering_end_at = Column(DateTime(timezone=True), nullable=True)
    discharge_start_at = Column(DateTime(timezone=True), nullable=True)
    discharge_end_at = Column(DateTime(timezone=True), nullable=True)

    status = Column(SAEnum(VesselActivityStatus, name="vessel_activity_status"), default=VesselActivityStatus.pending, nullable=False)
    notes = Column(Text, nullable=True)
    completion_notes = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # ── Per-vessel stage flow (cast off -> discharge completed) — additive,
    # independent of `status` above. Every timestamp is caller-supplied
    # (never forced to "now"), since stages are routinely logged after the
    # fact. Prefixed `stage_*` to avoid colliding with the older
    # bunkering_start_at/discharge_start_at columns above, which serve a
    # different (truck -> barge) leg and stay untouched.
    stage = Column(SAEnum(VesselStage, name="vessel_stage"), nullable=True)
    stage_cast_off_at = Column(DateTime(timezone=True), nullable=True)
    stage_outbound_at = Column(DateTime(timezone=True), nullable=True)
    stage_alongside_at = Column(DateTime(timezone=True), nullable=True)
    stage_hse_check_at = Column(DateTime(timezone=True), nullable=True)
    stage_discharging_at = Column(DateTime(timezone=True), nullable=True)
    stage_discharge_completed_at = Column(DateTime(timezone=True), nullable=True)

    # ── HSE checklist — non-blocking record, same {item, result, notes}
    # shape as TruckSafetyAudit.checklist.
    hse_checklist = Column(JSONB, default=list, nullable=False)
    hse_result = Column(SAEnum(AuditResult, name="audit_result"), nullable=True)
    hse_conducted_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    hse_conducted_at = Column(DateTime(timezone=True), nullable=True)
    hse_notes = Column(Text, nullable=True)
    hse_safety_officer = Column(String(200), nullable=True)

    # ── Discharge-completion arithmetic — system calculates gsv/mt_vacuum
    # from the submitted readings, litres-based (spec's BDN convention).
    # Written by the OLD stage-based flow (full_operation) via
    # record_discharge_quantities, and reused as-is by the NEW vessel-only
    # commence/updates/complete/quantities flow's record_quantities — each
    # VesselActivity row belongs to exactly one operation of one type, so
    # there's no collision between the two writers.
    gov = Column(Numeric(14, 2), nullable=True)
    vcf = Column(Numeric(8, 4), nullable=True)
    gsv = Column(Numeric(14, 2), nullable=True)
    mt_vacuum = Column(Numeric(12, 3), nullable=True)

    # ── Vessel-only Loading Commenced/Completed — additive, independent of
    # both `status`/`started_at`/`completed_at` (the old ROB-session flow)
    # and `stage`/`stage_*_at` (the full_operation 6-stage flow) above.
    # Every pair below always stores BOTH the server-captured instant and
    # the caller's own stated time — never one overwriting the other.
    # Reused as-is for "Loading Commenced"/"Loading Completed" in the
    # six-stage + multiple-receiving-vessel-legs rebuild — loading happens
    # exactly once per barge run, same cardinality as commence/complete
    # always had, so no new columns were needed for this part.
    commence_system_at = Column(DateTime(timezone=True), nullable=True)
    commence_user_at = Column(DateTime(timezone=True), nullable=True)
    commence_description = Column(Text, nullable=True)
    complete_system_at = Column(DateTime(timezone=True), nullable=True)
    complete_user_at = Column(DateTime(timezone=True), nullable=True)
    complete_description = Column(Text, nullable=True)

    # ── Superseded — these four (migration 039) held the vessel-only
    # flow's single-shot discharge/received figures back when one
    # VesselActivity meant one delivery. The six-stage + multi-leg rebuild
    # (migration 041) splits this: received quantity now lives in the
    # loading_* columns below (loading happens once), and discharged
    # quantity now lives per-leg on VesselActivityLeg (delivery repeats
    # per receiving vessel). Left in place, never dropped, but dead for
    # any row created after migration 041.
    discharged_quantity_litres = Column(Numeric(14, 2), nullable=True)
    received_quantity_litres = Column(Numeric(14, 2), nullable=True)
    quantity_recorded_at = Column(DateTime(timezone=True), nullable=True)
    quantity_description = Column(Text, nullable=True)

    # ── Loading Received Quantity — the one-time intake reading, taken
    # once loading is complete. Deliberately separate columns from the
    # gov/vcf/gsv/mt_vacuum/density block above (full_operation's, written
    # only by record_discharge_quantities) and from VesselActivityLeg's own
    # per-leg discharge readings below — three independent writers, three
    # independent column sets, no shared-column ambiguity.
    loading_received_quantity_litres = Column(Numeric(14, 2), nullable=True)
    loading_density = Column(Numeric(8, 4), nullable=True)
    loading_temperature_before_loading = Column(Numeric(6, 2), nullable=True)
    loading_temperature_after_loading = Column(Numeric(6, 2), nullable=True)
    loading_vcf = Column(Numeric(8, 4), nullable=True)
    loading_gov = Column(Numeric(14, 2), nullable=True)
    loading_gsv = Column(Numeric(14, 2), nullable=True)         # computed = gov*vcf
    loading_mt_vacuum = Column(Numeric(12, 3), nullable=True)   # computed = gsv*density
    loading_quantity_recorded_at = Column(DateTime(timezone=True), nullable=True)
    loading_quantity_description = Column(Text, nullable=True)

    # Relationships
    operation = relationship("Operation", back_populates="vessel_activities")
    vessel = relationship("Vessel", back_populates="vessel_activities")
    assignee = relationship("User", foreign_keys=[assigned_to])
    assigner = relationship("User", foreign_keys=[assigned_by])
    hse_conductor = relationship("User", foreign_keys=[hse_conducted_by])
    legs = relationship("VesselActivityLeg", back_populates="vessel_activity", cascade="all, delete-orphan", order_by="VesselActivityLeg.created_at")
    comments = relationship("VesselActivityComment", back_populates="vessel_activity", cascade="all, delete-orphan", order_by="VesselActivityComment.recorded_at")
    updates = relationship("VesselActivityUpdate", back_populates="vessel_activity", cascade="all, delete-orphan", order_by="VesselActivityUpdate.recorded_at.desc()")


class VesselActivityComment(Base):
    """Append-only free-text comment log, optionally tied to a stage —
    comments accumulate over time, so this is a child table rather than a
    handful of nullable columns."""
    __tablename__ = "vessel_activity_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vessel_activity_id = Column(UUID(as_uuid=True), ForeignKey("vessel_activities.id", ondelete="CASCADE"), nullable=False)
    stage = Column(SAEnum(VesselStage, name="vessel_stage"), nullable=True)  # null = general comment
    comment = Column(Text, nullable=False)
    recorded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    recorded_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # BM correction markers — a corrected record stays visible as corrected
    # rather than silently differing from what was originally posted.
    edited_at = Column(DateTime(timezone=True), nullable=True)
    edited_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    edit_reason = Column(Text, nullable=True)

    vessel_activity = relationship("VesselActivity", back_populates="comments")
    recorder = relationship("User", foreign_keys=[recorded_by])
    editor = relationship("User", foreign_keys=[edited_by])


class VesselActivityUpdate(Base):
    """Free-form operational update for the vessel-only commence/updates/
    complete/quantities flow — content + optional image, always
    system-timestamped (never user-entered). Deliberately a separate table
    from VesselActivityComment rather than extending it: `stage` would sit
    permanently NULL for every vessel-only row, and mixing genuine
    operational updates with ad-hoc general comments would make the comment
    list ambiguous. Zero effect on the full_operation comment flow."""
    __tablename__ = "vessel_activity_updates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vessel_activity_id = Column(UUID(as_uuid=True), ForeignKey("vessel_activities.id", ondelete="CASCADE"), nullable=False)
    # Optional tag to a specific receiving-vessel leg — null means a
    # general/loading-level update, same nullable-tag convention as
    # VesselActivityComment.stage above.
    leg_id = Column(UUID(as_uuid=True), ForeignKey("vessel_activity_legs.id", ondelete="CASCADE"), nullable=True)
    content = Column(Text, nullable=False)
    image_url = Column(Text, nullable=True)
    recorded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    recorded_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # BM correction markers — see VesselActivityComment above.
    edited_at = Column(DateTime(timezone=True), nullable=True)
    edited_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    edit_reason = Column(Text, nullable=True)

    vessel_activity = relationship("VesselActivity", back_populates="updates")
    leg = relationship("VesselActivityLeg", foreign_keys=[leg_id])
    recorder = relationship("User", foreign_keys=[recorded_by])
    editor = relationship("User", foreign_keys=[edited_by])


class VesselActivityLeg(Base):
    """One receiving vessel's independent delivery leg under a vessel-only
    VesselActivity (the loading/barge run). Loading happens once (tracked
    on the parent VesselActivity's commence/complete pair + loading_*
    columns); delivery repeats per receiving vessel, each leg running its
    own Cast Off -> Alongside -> Discharge Commenced -> Discharge Completed
    sequence, own HSE record, own discharge readings, and own Vessel BDN.
    The BM can add a leg at any point, including after other legs on the
    same activity have already reached Discharge Completed."""
    __tablename__ = "vessel_activity_legs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vessel_activity_id = Column(UUID(as_uuid=True), ForeignKey("vessel_activities.id", ondelete="CASCADE"), nullable=False)
    receiving_vessel_name = Column(String(200), nullable=False)
    imo_number = Column(String(20), nullable=True)
    eta_at = Column(DateTime(timezone=True), nullable=True)   # single working value, updated in place by BM/MM
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # ── 4-stage dual timestamp tracker — every stage stores BOTH the
    # server-captured instant and the caller's own stated time, same
    # convention as the parent activity's commence/complete pair.
    stage = Column(SAEnum(VesselLegStage, name="vessel_leg_stage"), nullable=True)
    stage_cast_off_system_at = Column(DateTime(timezone=True), nullable=True)
    stage_cast_off_user_at = Column(DateTime(timezone=True), nullable=True)
    stage_alongside_system_at = Column(DateTime(timezone=True), nullable=True)
    stage_alongside_user_at = Column(DateTime(timezone=True), nullable=True)
    stage_discharge_commenced_system_at = Column(DateTime(timezone=True), nullable=True)
    stage_discharge_commenced_user_at = Column(DateTime(timezone=True), nullable=True)
    stage_discharge_completed_system_at = Column(DateTime(timezone=True), nullable=True)
    stage_discharge_completed_user_at = Column(DateTime(timezone=True), nullable=True)

    # ── Per-leg HSE — a fresh safety check for each ship-to-ship
    # encounter, non-blocking (a failed item never blocks progress).
    hse_checklist = Column(JSONB, default=list, nullable=False)
    hse_result = Column(SAEnum(AuditResult, name="audit_result"), nullable=True)
    hse_conducted_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    hse_conducted_at = Column(DateTime(timezone=True), nullable=True)
    hse_notes = Column(Text, nullable=True)
    hse_safety_officer = Column(String(200), nullable=True)

    # ── Per-leg discharge & quality readings — same field names/units as
    # VesselBdn for consistency. gsv/mt_vacuum are system-computed.
    quantity_discharged_litres = Column(Numeric(14, 2), nullable=True)
    density = Column(Numeric(8, 4), nullable=True)
    temperature_before_loading = Column(Numeric(6, 2), nullable=True)
    temperature_after_loading = Column(Numeric(6, 2), nullable=True)
    vcf = Column(Numeric(8, 4), nullable=True)
    gov = Column(Numeric(14, 2), nullable=True)
    gsv = Column(Numeric(14, 2), nullable=True)          # computed = gov*vcf
    mt_vacuum = Column(Numeric(12, 3), nullable=True)    # computed = gsv*density
    quantity_recorded_at = Column(DateTime(timezone=True), nullable=True)
    quantity_description = Column(Text, nullable=True)

    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_reason = Column(Text, nullable=True)

    vessel_activity = relationship("VesselActivity", back_populates="legs")
    creator = relationship("User", foreign_keys=[created_by])
    hse_conductor = relationship("User", foreign_keys=[hse_conducted_by])
