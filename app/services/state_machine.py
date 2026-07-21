from typing import Dict, List, Optional
from app.models.enums import OperationStatus, OperationType, UserRole


# ── Transition maps per operation type ────────────────────────────────────────
#
# COMMERCIAL FLOW (all types):
#   Finance (PFI, payments, invoices, vouchers) is now a fully standalone
#   concern, managed from its own portal — it does NOT gate or appear in the
#   operation's own status pipeline. An operation runs its operational
#   lifecycle (tasks → feedback → active → delivery/vessel ops → BDN →
#   invoice → completed) independently of when/whether Finance has recorded
#   a payment. Payment/PFI records still reference operation_id for
#   reporting/traceability, they just never drive the operation's status.
#
# TRUCK ONLY:
#   draft → tasks_assigned → awaiting_feedback → feedback_submitted
#   → active → pending_completion (delivery done) → invoiced → completed → archived
#
# FULL OPERATION:
#   draft → tasks_assigned → awaiting_feedback → feedback_submitted
#   → active → vessel_operations → bdn_pending → bdn_approved → invoiced → completed → archived
#
# VESSEL ONLY:
#   draft → tasks_assigned → active
#   → vessel_operations → bdn_pending → bdn_approved → invoiced → completed → archived
#
# BACKWARD COMPAT paths (payment_processing/payment_confirmed/pfi_linked are
# retired from the primary flow — these entries exist ONLY so an operation
# that was already sitting in one of those statuses before this change can
# still be moved forward; nothing new reaches them going forward):
#   active → pfi_linked/payment_processing/payment_confirmed  — REMOVED, no
#     longer reachable from "active" at all.
#   pfi_linked → payment_processing → payment_confirmed → (pending_completion
#     | vessel_operations | invoiced)  — legacy chain, still walkable end-to-end.
#   bdn_approved → pfi_linked  (old: PFI after BDN)

TRUCK_ONLY_TRANSITIONS: Dict[str, List[str]] = {
    "draft":              ["tasks_assigned", "cancelled"],
    "tasks_assigned":     ["awaiting_feedback", "cancelled"],
    "awaiting_feedback":  ["feedback_submitted"],
    "feedback_submitted": ["active", "feedback_rejected"],
    "feedback_rejected":  ["feedback_submitted"],
    "feedback_approved":  ["active"],                            # legacy compat
    # Primary path: operation runs independently of finance/payment.
    "active":             ["pending_completion", "cancelled"],
    "pfi_linked":         ["payment_processing"],                 # legacy compat chain only
    "payment_processing": ["payment_confirmed"],                  # legacy compat chain only
    "payment_confirmed":  ["pending_completion", "invoiced"],     # legacy compat chain only
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
    # Primary path: operation runs independently of finance/payment.
    "active":             ["vessel_operations", "cancelled"],
    "pfi_linked":         ["payment_processing"],                 # legacy compat chain only
    "payment_processing": ["payment_confirmed"],                  # legacy compat chain only
    "payment_confirmed":  ["vessel_operations", "invoiced"],      # legacy compat chain only
    "vessel_operations":  ["bdn_pending"],
    "bdn_pending":        ["bdn_approved", "vessel_operations"],
    "bdn_approved":       ["invoiced", "pfi_linked", "vessel_operations"],   # pfi_linked = legacy compat
    "invoiced":           ["completed"],
    "completed":          ["archived"],
}

VESSEL_ONLY_TRANSITIONS: Dict[str, List[str]] = {
    "draft":              ["tasks_assigned", "cancelled"],
    "tasks_assigned":     ["active", "cancelled"],               # no truck feedback for vessel-only
    # Primary path: operation runs independently of finance/payment.
    "active":             ["vessel_operations", "cancelled"],
    "pfi_linked":         ["payment_processing"],                 # legacy compat chain only
    "payment_processing": ["payment_confirmed"],                  # legacy compat chain only
    "payment_confirmed":  ["vessel_operations", "invoiced"],      # legacy compat chain only
    "vessel_operations":  ["bdn_pending"],
    "bdn_pending":        ["bdn_approved", "vessel_operations"],
    "bdn_approved":       ["invoiced", "pfi_linked", "vessel_operations"],   # pfi_linked = legacy compat
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

    # Legacy compat chain only — "active" no longer offers a path into these,
    # kept so an operation already sitting in one of these statuses can still
    # be moved forward by Finance.
    "pfi_linked->payment_processing":   ["finance_manager"],
    "payment_processing->payment_confirmed": ["finance_manager"],

    # ── Vessel operations — primary path now (finance/payment no longer gates it) ──
    "active->vessel_operations":        ["ops_supervisor", "bunker_manager"],
    "payment_confirmed->vessel_operations": ["ops_supervisor", "bunker_manager"],   # legacy compat
    "vessel_operations->bdn_pending":   ["bunker_manager", "marine_manager"],
    "bdn_pending->bdn_approved":        ["bunker_manager"],
    "bdn_pending->vessel_operations":   ["bunker_manager"],
    "bdn_approved->vessel_operations":  ["system", "bunker_manager"],

    # ── Truck delivery completion — primary path now (finance/payment no longer gates it) ──
    "active->pending_completion":       ["logistics_officer", "ops_supervisor"],
    "payment_confirmed->pending_completion": ["logistics_officer", "ops_supervisor"],   # legacy compat
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
