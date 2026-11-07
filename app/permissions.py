"""Who counts as an operation manager.

The Ops Supervisor holds the Bunker Manager's authority **inside an
operation, and nowhere else**: editing it, moving it through its pipeline,
running vessel stages, correcting recorded figures, notifying clients. The
BM keeps everything outside that boundary to themselves — user admin, the
fleet and vessel registries, licences, finance, and the document hub.

Approving and rejecting BDNs is included, on the BM's explicit instruction.
Note what that means in practice: the Ops Supervisor is also one of the roles
that *submits* Truck and Vessel BDNs, so the same person can now submit a set
of figures and sign them off. Every approval is still recorded against the
user who made it, so the audit trail shows when that happened.

This module holds no imports beyond the role enum so that routers,
dependencies and services can all share one definition without importing
each other.
"""

from app.models.enums import UserRole

# Roles that may act on an operation with the BM's authority. Order matters
# only for the error message require_roles produces.
OPERATION_MANAGER_ROLES = (UserRole.bunker_manager, UserRole.ops_supervisor)

# Roles that see every operation, whoever it is assigned to. Everyone else
# sees only the operations they hold a task or vessel run on (clients: their
# own). The Safety Officer joined on 1 Nov 2026 to watch every operation;
# being here gives sight, not authority — see WATCH_ONLY_ROLES.
OPERATION_VIEWER_ROLES = (
    UserRole.bunker_manager,
    UserRole.finance_manager,
    UserRole.ops_supervisor,
    UserRole.safety_officer,
)

# Roles that watch operations but never change their status — not even the
# "system" moves that any working role may otherwise trigger (see
# StateMachine.can_user_transition). The Safety Officer raises an incident
# and the BM decides; HR only reviews performance.
WATCH_ONLY_ROLES = (UserRole.safety_officer, UserRole.hr)

# Roles that may see every staff member's named KPI score, read only: the
# leaderboard, the company trend and the HR pack. Management agreed on
# 1 Nov 2026 that HR joins the BM here. Grading, month close and the full
# Command Center (money, fleet, licences) stay with the BM alone.
PERFORMANCE_VIEWER_ROLES = (UserRole.bunker_manager, UserRole.hr)

# Vessel incident and maintenance logs (Required System Update, item 1).
# Everyone who works the barges can read them and report an incident; only
# the BM and the Safety Officer move an incident to investigation, close it
# or reopen it (agreed 1 Nov 2026). The same four roles keep the
# maintenance log. Finance, HR, Truck Operation and clients have no part.
VESSEL_RECORD_ROLES = (
    UserRole.bunker_manager,
    UserRole.ops_supervisor,
    UserRole.cargo_superintendent,
    UserRole.safety_officer,
)
INCIDENT_CLOSER_ROLES = (UserRole.bunker_manager, UserRole.safety_officer)

# Inspections and compliance checks (item 2). The Safety Officer and the BM
# log them and sign off their follow-up actions; the Ops Supervisor reads
# them, since the fixes usually land in their operations.
INSPECTION_ROLES = (UserRole.bunker_manager, UserRole.safety_officer)
INSPECTION_VIEWER_ROLES = (UserRole.bunker_manager, UserRole.ops_supervisor, UserRole.safety_officer)

# The safety dashboard: open incidents, failed checks, missing truck audits,
# open truck issues, maintenance due and inspection actions, fleet-wide.
SAFETY_DASHBOARD_ROLES = (UserRole.bunker_manager, UserRole.safety_officer)


def is_operation_manager(user) -> bool:
    """True when `user` may take a Bunker-Manager-level action on an operation.

    Mirrors `require_roles` exactly: a real Bunker Manager always passes,
    whatever they are currently acting as; anyone else is judged on their
    acted-as role if they have one, otherwise their real role.
    """
    if user.role == UserRole.bunker_manager:
        return True
    effective = getattr(user, "acting_as_role", None) or user.role
    return effective in OPERATION_MANAGER_ROLES
