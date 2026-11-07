"""Watch-only roles (Safety Officer, HR) can never move an operation.

Pure logic — no database, no network.

Run:  cd reliant-anchor-api && ./venv/Scripts/python.exe -m pytest tests -q
"""
import pytest

from app.models.enums import OperationStatus, OperationType, UserRole
from app.permissions import WATCH_ONLY_ROLES
from app.services.state_machine import (
    TRANSITION_PERMISSIONS, StateMachine, StateMachineError,
)

ALL_MOVES = [
    (OperationStatus(a), OperationStatus(b))
    for a, b in (k.split("->") for k in TRANSITION_PERMISSIONS if not k.startswith("ANY"))
]

# The five moves marked "system", which any other role may make.
SYSTEM_MOVES = [
    (OperationStatus(a), OperationStatus(b))
    for k, roles in TRANSITION_PERMISSIONS.items() if "system" in roles
    for a, b in [k.split("->")]
]


@pytest.mark.parametrize("role", WATCH_ONLY_ROLES)
@pytest.mark.parametrize("move", ALL_MOVES, ids=lambda m: f"{m[0].value}->{m[1].value}")
def test_watch_only_role_cannot_make_any_move(role, move):
    assert not StateMachine.can_user_transition(move[0], move[1], role)


@pytest.mark.parametrize("role", WATCH_ONLY_ROLES)
def test_watch_only_role_cannot_cancel(role):
    assert not StateMachine.can_user_transition(OperationStatus.active, OperationStatus.cancelled, role)


@pytest.mark.parametrize("role", WATCH_ONLY_ROLES)
def test_watch_only_role_sees_no_buttons(role):
    for status in OperationStatus:
        for op_type in OperationType:
            assert StateMachine.get_available_transitions(op_type, status, role) == []


@pytest.mark.parametrize("role", WATCH_ONLY_ROLES)
def test_validate_refuses_archive(role):
    with pytest.raises(StateMachineError):
        StateMachine.validate_transition(
            OperationType.full_operation, OperationStatus.completed, OperationStatus.archived, role,
        )


def test_system_moves_still_open_to_working_roles():
    # The vessel stage flow relies on these: whoever finishes the last
    # discharge moves the operation on. That must not change.
    assert len(SYSTEM_MOVES) == 5
    for a, b in SYSTEM_MOVES:
        assert StateMachine.can_user_transition(a, b, UserRole.marine_operator)
        assert StateMachine.can_user_transition(a, b, UserRole.cargo_superintendent)


def test_labels():
    from app.models.enums import role_label
    assert role_label(UserRole.safety_officer) == "Safety Officer"
    assert role_label("hr") == "HR"
