"""Shared field-level diff builder for edit-audit-trail AuditLog entries.

Extracted from the pattern duplicated in operation_service.update_operation and
truck_service.update_truck: model_dump(exclude_unset=True) -> compare old/new ->
{field: {"from": ..., "to": ...}} -> setattr.
"""
from typing import Any, Dict


def capture_diff(instance: Any, update_data: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    """Compare update_data against instance's current attribute values, apply the
    update via setattr, and return a {field: {from, to}} dict of what changed."""
    changes: Dict[str, Dict[str, str]] = {}
    for field, value in update_data.items():
        if hasattr(value, "value"):  # enum
            value = value.value
        old_val = getattr(instance, field, None)
        if hasattr(old_val, "value"):  # old_val may still be a live enum instance —
            old_val = old_val.value    # unwrap it the same way, or str(old_val) prints
                                        # "ClassName.member" (str+Enum's default __str__)
                                        # and never equals the already-unwrapped new value,
                                        # flagging every unchanged enum field as "changed".
        if str(old_val) != str(value):
            changes[field] = {"from": str(old_val), "to": str(value)}
        setattr(instance, field, value)
    return changes
