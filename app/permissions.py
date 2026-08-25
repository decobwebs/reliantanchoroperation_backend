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
