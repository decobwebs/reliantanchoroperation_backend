from typing import Dict, List, Optional
from app.models.enums import OperationStatus, OperationType, UserRole


# ── Transition maps per operation type ────────────────────────────────────────
#
# COMMERCIAL FLOW (all types):
#   PFI (advance payment from client) is linked at operation CREATION time now
#   (or added later via a BM correction) — it is no longer a manual workflow
#   step, so "active" moves straight into payment_processing the moment
#   Finance records a payment (PaymentService.record_payment triggers this
#   transition itself — there is no BM-actionable button for it).
#   BDN (actual delivery record) drives the Tax Invoice.
#   Invoice closes the operation — it is the final billing document.
#
# TRUCK ONLY:
#   draft → tasks_assigned → awaiting_feedback → feedback_submitted
#   → active → payment_processing → payment_confirmed
#   → pending_completion (delivery done) → invoiced → completed → archived
#
# FULL OPERATION:
#   draft → tasks_assigned → awaiting_feedback → feedback_submitted
#   → active → payment_processing → payment_confirmed
#   → vessel_operations → bdn_pending → bdn_approved → invoiced → completed → archived
#
# VESSEL ONLY:
#   draft → tasks_assigned → active → payment_processing → payment_confirmed
#   → vessel_operations → bdn_pending → bdn_approved → invoiced → completed → archived
#
# BACKWARD COMPAT paths (for operations created under the old flow, or already
# sitting in a since-retired status):
#   pfi_linked → payment_processing  (old: explicit "Link PFI" step — no longer
#     reachable from "active" going forward, kept only so any operation still
#     sitting in pfi_linked from before this change can still progress)
#   bdn_approved → pfi_linked  (old: PFI after BDN)
#   payment_confirmed → invoiced  (old: Invoice directly after payment, no delivery step)
#   active → vessel_operations  (old: no PFI gate before ops)

TRUCK_ONLY_TRANSITIONS: Dict[str, List[str]] = {
    "draft":              ["tasks_assigned", "cancelled"],
    "tasks_assigned":     ["awaiting_feedback", "cancelled"],
    "awaiting_feedback":  ["feedback_submitted"],
    "feedback_submitted": ["active", "feedback_rejected"],
    "feedback_rejected":  ["feedback_submitted"],
    "feedback_approved":  ["active"],                            # legacy compat
    # Primary path: payment → delivery → invoice (PFI is linked at creation)
    "active":             ["payment_processing", "pending_completion", "cancelled"],  # pending_completion = compat
    "pfi_linked":         ["payment_processing"],                 # legacy compat only
    "payment_processing": ["payment_confirmed"],
    "payment_confirmed":  ["pending_completion", "invoiced"],    # invoiced = compat skip
    "pending_completion": ["invoiced", "active"],
    "invoiced":           ["completed"],
    "completed":          ["archived"],
}

FULL_OPERATION_TRANSITIONS: Dict[str, List[str]] = {
    "draft":              ["tasks_assigned", "cancelled"],
    "tasks_assigned":     ["awaiting_feedback", "cancelled"],
    "awaiting_feedback":  ["feedback_submitted"],
    "feedback_submitted": ["active", "feedback_rejected"],
    "feedback_rejected":  ["feedback_submitted"],
    "feedback_approved":  ["active"],                            # legacy compat
    # Primary path: payment → ops → BDN → invoice (PFI is linked at creation)
    "active":             ["payment_processing", "vessel_operations", "cancelled"],   # vessel_operations = compat
    "pfi_linked":         ["payment_processing"],                 # legacy compat only
    "payment_processing": ["payment_confirmed"],
    "payment_confirmed":  ["vessel_operations", "invoiced"],     # vessel_operations = new, invoiced = compat
    "vessel_operations":  ["bdn_pending"],
    "bdn_pending":        ["bdn_approved", "vessel_operations"],
    "bdn_approved":       ["invoiced", "pfi_linked", "vessel_operations"],   # invoiced = new, pfi_linked = compat
    "invoiced":           ["completed"],
    "completed":          ["archived"],
}

VESSEL_ONLY_TRANSITIONS: Dict[str, List[str]] = {
    "draft":              ["tasks_assigned", "cancelled"],
    "tasks_assigned":     ["active", "cancelled"],               # no truck feedback for vessel-only
    # Primary path: payment → ops → BDN → invoice (PFI is linked at creation)
    "active":             ["payment_processing", "vessel_operations", "cancelled"],   # vessel_operations = compat
    "pfi_linked":         ["payment_processing"],                 # legacy compat only
    "payment_processing": ["payment_confirmed"],
    "payment_confirmed":  ["vessel_operations", "invoiced"],     # vessel_operations = new, invoiced = compat
    "vessel_operations":  ["bdn_pending"],
    "bdn_pending":        ["bdn_approved", "vessel_operations"],
    "bdn_approved":       ["invoiced", "pfi_linked", "vessel_operations"],   # invoiced = new, pfi_linked = compat
    "invoiced":           ["completed"],
    "completed":          ["archived"],
}

# ── Transition permission map ──────────────────────────────────────────────────

TRANSITION_PERMISSIONS: Dict[str, List[str]] = {
    # Creation & assignment
    "draft->tasks_assigned":            ["bunker_manager"],
    "tasks_assigned->awaiting_feedback":["bunker_manager", "system"],
    "tasks_assigned->active":           ["bunker_manager"],           # vessel-only direct activation

    # Truck feedback loop
    "awaiting_feedback->feedback_submitted":  ["logistics_officer"],
    "feedback_submitted->active":             ["bunker_manager"],
    "feedback_submitted->feedback_rejected":  ["bunker_manager"],
    "feedback_rejected->feedback_submitted":  ["logistics_officer"],
    "feedback_approved->active":              ["bunker_manager"],     # legacy compat

    # ── Old compat: BM links PFI after BDN (operations that pre-date redesign)
    "bdn_approved->pfi_linked":         ["bunker_manager"],

    # Payment processing — PFI is linked at operation creation now, so payment
    # can be recorded (and this transition triggered) directly from "active".
    "active->payment_processing":       ["finance_manager"],
    "pfi_linked->payment_processing":   ["finance_manager"],   # legacy compat only
    "payment_processing->payment_confirmed": ["finance_manager"],

    # ── Vessel operations (only after payment confirmed in new flow) ──────────
    "active->vessel_operations":        ["ops_supervisor", "bunker_manager"],    # compat
    "payment_confirmed->vessel_operations": ["ops_supervisor", "bunker_manager"],
    "vessel_operations->bdn_pending":   ["bunker_manager", "marine_manager"],
    "bdn_pending->bdn_approved":        ["bunker_manager"],
    "bdn_pending->vessel_operations":   ["bunker_manager"],
    "bdn_approved->vessel_operations":  ["system", "bunker_manager"],

    # ── Truck delivery completion ─────────────────────────────────────────────
    "active->pending_completion":       ["logistics_officer", "ops_supervisor"], # compat
    "payment_confirmed->pending_completion": ["logistics_officer", "ops_supervisor"],
    "pending_completion->active":       ["bunker_manager"],
    "pending_completion->invoiced":     ["finance_manager"],

    # ── Invoicing ─────────────────────────────────────────────────────────────
    # New path: BDN approved → invoice (FM raises final invoice based on actual BDN)
    "bdn_approved->invoiced":           ["finance_manager"],
    # Old compat: payment confirmed → invoiced (skips delivery step)
    "payment_confirmed->invoiced":      ["finance_manager"],

    # Closure
    "invoiced->completed":              ["bunker_manager"],
    "completed->archived":              ["bunker_manager", "system"],

    # Emergency cancel from any non-terminal status
    "ANY->cancelled":                   ["bunker_manager"],
}

_TYPE_MAP: Dict[str, Dict[str, List[str]]] = {
    OperationType.full_operation: FULL_OPERATION_TRANSITIONS,
    OperationType.vessel_only:    VESSEL_ONLY_TRANSITIONS,
    OperationType.truck_only:     TRUCK_ONLY_TRANSITIONS,
}


class StateMachineError(Exception):
    pass


class StateMachine:
    """Validates and executes operation state transitions."""

    @staticmethod
    def get_transition_map(operation_type: OperationType) -> Dict[str, List[str]]:
        return _TYPE_MAP.get(operation_type, FULL_OPERATION_TRANSITIONS)

    @staticmethod
    def is_valid_transition(
        operation_type: OperationType,
        from_status: OperationStatus,
        to_status: OperationStatus,
    ) -> bool:
        terminal = {OperationStatus.cancelled, OperationStatus.archived}
        if to_status == OperationStatus.cancelled and from_status not in terminal:
            return True

        transition_map = StateMachine.get_transition_map(operation_type)
        allowed = transition_map.get(from_status.value, [])
        return to_status.value in allowed

    @staticmethod
    def can_user_transition(
        from_status: OperationStatus,
        to_status: OperationStatus,
        user_role: UserRole,
    ) -> bool:
        key = f"{from_status.value}->{to_status.value}"
        allowed_roles = TRANSITION_PERMISSIONS.get(key)

        if allowed_roles is None:
            if to_status == OperationStatus.cancelled:
                allowed_roles = TRANSITION_PERMISSIONS.get("ANY->cancelled", [])
            else:
                return False

        return user_role.value in allowed_roles or "system" in allowed_roles

    @staticmethod
    def validate_transition(
        operation_type: OperationType,
        from_status: OperationStatus,
        to_status: OperationStatus,
        user_role: UserRole,
    ) -> None:
        if not StateMachine.is_valid_transition(operation_type, from_status, to_status):
            raise StateMachineError(
                f"Transition from '{from_status.value}' to '{to_status.value}' "
                f"is not valid for operation type '{operation_type.value}'"
            )
        if not StateMachine.can_user_transition(from_status, to_status, user_role):
            raise StateMachineError(
                f"Role '{user_role.value}' is not permitted to transition "
                f"from '{from_status.value}' to '{to_status.value}'"
            )

    @staticmethod
    def get_available_transitions(
        operation_type: OperationType,
        current_status: OperationStatus,
        user_role: UserRole,
    ) -> List[str]:
        transition_map = StateMachine.get_transition_map(operation_type)
        possible = transition_map.get(current_status.value, [])

        terminal = {OperationStatus.cancelled.value, OperationStatus.archived.value}
        if current_status.value not in terminal and OperationStatus.cancelled.value not in possible:
            possible = possible + [OperationStatus.cancelled.value]

        return [
            s for s in possible
            if StateMachine.can_user_transition(
                current_status, OperationStatus(s), user_role
            )
        ]
