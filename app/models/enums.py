import enum


class AuditResult(str, enum.Enum):
    satisfactory = "satisfactory"
    not_satisfactory = "not_satisfactory"


class UserRole(str, enum.Enum):
    bunker_manager = "bunker_manager"
    ops_supervisor = "ops_supervisor"
    logistics_officer = "logistics_officer"
    marine_manager = "marine_manager"
    finance_manager = "finance_manager"
    client = "client"


class OperationType(str, enum.Enum):
    truck_only = "truck_only"
    vessel_only = "vessel_only"
    full_operation = "full_operation"


class OperationStatus(str, enum.Enum):
    draft = "draft"
    tasks_assigned = "tasks_assigned"
    awaiting_feedback = "awaiting_feedback"
    feedback_submitted = "feedback_submitted"
    feedback_approved = "feedback_approved"    # legacy — compat transition → active
    feedback_rejected = "feedback_rejected"
    active = "active"                          # operation is live / in action
    pending_completion = "pending_completion"  # supervisor submitted completion report
    vessel_operations = "vessel_operations"
    bdn_pending = "bdn_pending"
    bdn_approved = "bdn_approved"
    pfi_linked = "pfi_linked"
    payment_processing = "payment_processing"
    payment_confirmed = "payment_confirmed"
    invoiced = "invoiced"
    completed = "completed"
    archived = "archived"
    cancelled = "cancelled"


class ProductType(str, enum.Enum):
    AGO = "AGO"            # Automotive Gas Oil (Diesel)
    DPK = "DPK"            # Dual Purpose Kerosene
    PMS = "PMS"            # Premium Motor Spirit (Petrol)
    HFO = "HFO"            # Heavy Fuel Oil
    VLSFO = "VLSFO"        # Very Low Sulphur Fuel Oil 0.5%
    LSMGO = "LSMGO"        # Low Sulphur Marine Gas Oil
    MGO = "MGO"            # Marine Gas Oil
    IFO_380 = "IFO_380"    # Intermediate Fuel Oil 380 cSt
    IFO_180 = "IFO_180"    # Intermediate Fuel Oil 180 cSt
    ULSFO = "ULSFO"        # Ultra Low Sulphur Fuel Oil
    JET_A1 = "JET_A1"      # Jet A-1 / Aviation Fuel
    ATK = "ATK"            # Aviation Turbine Kerosene
    NAPHTHA = "NAPHTHA"    # Naphtha
    CRUDE = "CRUDE"        # Crude Oil
    OTHER = "OTHER"        # Any other product (specify in notes)


class TaskType(str, enum.Enum):
    truck_logistics = "truck_logistics"
    vessel_operations = "vessel_operations"
    marine_discharge = "marine_discharge"
    finance_processing = "finance_processing"


class TaskStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class Priority(str, enum.Enum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class TruckWaiverStatus(str, enum.Enum):
    available = "available"
    linked = "linked"


class AuditPhase(str, enum.Enum):
    pre = "pre"
    post = "post"


class TruckStatus(str, enum.Enum):
    available = "available"
    assigned = "assigned"
    in_transit = "in_transit"
    discharging = "discharging"
    maintenance = "maintenance"
    out_of_service = "out_of_service"


class TruckOpStatus(str, enum.Enum):
    pending = "pending"
    loading = "loading"
    in_transit = "in_transit"
    arrived = "arrived"
    discharging = "discharging"
    completed = "completed"
    cancelled = "cancelled"


class VesselStatus(str, enum.Enum):
    available = "available"
    assigned = "assigned"
    operating = "operating"
    maintenance = "maintenance"


class RobEntryType(str, enum.Enum):
    initial = "initial"
    discharge = "discharge"
    replenishment = "replenishment"
    adjustment = "adjustment"
    correction = "correction"


class BdnStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class PfiType(str, enum.Enum):
    client_proforma = "client_proforma"   # Reliant Anchor → Client (revenue document)
    supplier_invoice = "supplier_invoice" # Supplier → Reliant Anchor (expense document)


class PfiStatus(str, enum.Enum):
    pending = "pending"                     # PFI received, awaiting FM review
    confirmed = "confirmed"                 # FM confirmed PFI is valid (pre-payment)
    payment_initiated = "payment_initiated"
    paid = "paid"                           # Payment confirmed — ready for operation
    linked = "linked"                       # Linked to an active operation
    completed = "completed"                 # Operation completed
    cancelled = "cancelled"


class VoucherStatus(str, enum.Enum):
    draft = "draft"
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"


class VoucherCategory(str, enum.Enum):
    port_fees = "port_fees"
    demurrage = "demurrage"
    logistics = "logistics"
    bunker_purchase = "bunker_purchase"
    labour = "labour"
    agency_fees = "agency_fees"
    documentation = "documentation"
    customs = "customs"
    inspection = "inspection"
    other = "other"


class VesselActivityStatus(str, enum.Enum):
    pending = "pending"       # assigned, awaiting marine supervisor
    active = "active"         # supervisor started recording
    completed = "completed"   # all done, final report submitted
    cancelled = "cancelled"


class InvoiceStatus(str, enum.Enum):
    draft = "draft"
    sent = "sent"
    paid = "paid"
    overdue = "overdue"
    cancelled = "cancelled"


class FeedbackStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    resubmitted = "resubmitted"


class DocType(str, enum.Enum):
    bdn = "bdn"
    invoice = "invoice"
    payment_voucher = "payment_voucher"
    pfi = "pfi"
    report = "report"
    clearance = "clearance"
    other = "other"


class NotificationType(str, enum.Enum):
    task_assigned = "task_assigned"
    approval_needed = "approval_needed"
    approved = "approved"
    rejected = "rejected"
    payment_update = "payment_update"
    rob_alert = "rob_alert"
    bdn_ready = "bdn_ready"
    milestone = "milestone"
    system = "system"
    operation_active = "operation_active"      # operation went live
    completion_pending = "completion_pending"  # supervisor submitted completion report
    vessel_activity_assigned = "vessel_activity_assigned"   # marine manager assigned to vessel activity
    vessel_activity_completed = "vessel_activity_completed" # marine manager completed vessel activity
