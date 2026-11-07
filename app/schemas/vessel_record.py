from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator

from app.models.vessel_record import (
    INCIDENT_TYPES, INCIDENT_SEVERITIES, MAINTENANCE_TYPES, MAINTENANCE_STATUSES,
)


def _strip(v):
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _one_of(value: Optional[str], allowed: tuple, what: str) -> Optional[str]:
    if value is not None and value not in allowed:
        raise ValueError(f"Unknown {what} '{value}'. Use one of: {', '.join(allowed)}")
    return value


class _Reason(BaseModel):
    """Every change after a record is first written needs a written reason —
    it is kept on the audit log next to what changed."""
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v):
        return _strip(v)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v):
        if not v:
            raise ValueError("A reason is required")
        return v


# ── Incidents ──────────────────────────────────────────────────────────────────

class VesselIncidentCreate(BaseModel):
    occurred_at: datetime
    incident_type: str
    severity: str = "medium"
    title: str
    location: Optional[str] = None
    description: Optional[str] = None
    immediate_action: Optional[str] = None
    # Typed references, resolved in the service: the reporter knows the
    # numbers on the paperwork, not database ids.
    operation_number: Optional[str] = None
    activity_number: Optional[str] = None

    @field_validator(
        "title", "location", "description", "immediate_action",
        "operation_number", "activity_number", mode="before",
    )
    @classmethod
    def strip_text(cls, v):
        return _strip(v)

    @field_validator("title")
    @classmethod
    def title_required(cls, v):
        if not v:
            raise ValueError("A short summary of the incident is required")
        return v

    @field_validator("incident_type")
    @classmethod
    def valid_type(cls, v):
        return _one_of(v, INCIDENT_TYPES, "incident type")

    @field_validator("severity")
    @classmethod
    def valid_severity(cls, v):
        return _one_of(v, INCIDENT_SEVERITIES, "severity")


class VesselIncidentUpdate(_Reason):
    """Correct the details of an incident that is not closed yet."""
    occurred_at: Optional[datetime] = None
    incident_type: Optional[str] = None
    severity: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    immediate_action: Optional[str] = None

    @field_validator("title", "location", "description", "immediate_action", mode="before")
    @classmethod
    def strip_text(cls, v):
        return _strip(v)

    @field_validator("incident_type")
    @classmethod
    def valid_type(cls, v):
        return _one_of(v, INCIDENT_TYPES, "incident type")

    @field_validator("severity")
    @classmethod
    def valid_severity(cls, v):
        return _one_of(v, INCIDENT_SEVERITIES, "severity")


class VesselIncidentInvestigate(BaseModel):
    note: Optional[str] = None

    @field_validator("note", mode="before")
    @classmethod
    def strip_note(cls, v):
        return _strip(v)


class VesselIncidentClose(BaseModel):
    """Closing needs both a root cause and the fix (agreed 1 Nov 2026)."""
    root_cause: str
    corrective_action: str

    @field_validator("root_cause", "corrective_action", mode="before")
    @classmethod
    def strip_text(cls, v):
        return _strip(v)

    @field_validator("root_cause")
    @classmethod
    def root_cause_required(cls, v):
        if not v:
            raise ValueError("A root cause is required to close an incident")
        return v

    @field_validator("corrective_action")
    @classmethod
    def corrective_action_required(cls, v):
        if not v:
            raise ValueError("A corrective action is required to close an incident")
        return v


class VesselIncidentReopen(_Reason):
    pass


# ── Maintenance ────────────────────────────────────────────────────────────────

class _MaintenanceFields(BaseModel):
    @field_validator("title", "description", "performed_by", "cost_currency", mode="before", check_fields=False)
    @classmethod
    def strip_text(cls, v):
        return _strip(v)

    @field_validator("maintenance_type", check_fields=False)
    @classmethod
    def valid_type(cls, v):
        return _one_of(v, MAINTENANCE_TYPES, "maintenance type")

    @field_validator("status", check_fields=False)
    @classmethod
    def valid_status(cls, v):
        return _one_of(v, MAINTENANCE_STATUSES, "status")

    @field_validator("cost", check_fields=False)
    @classmethod
    def cost_not_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("Cost cannot be negative")
        return v

    @field_validator("cost_currency", check_fields=False)
    @classmethod
    def upper_currency(cls, v):
        return v.upper() if v else v


class VesselMaintenanceCreate(_MaintenanceFields):
    maintenance_type: str
    title: str
    description: Optional[str] = None
    status: str = "planned"
    planned_date: Optional[date] = None
    completed_date: Optional[date] = None
    next_due_date: Optional[date] = None
    performed_by: Optional[str] = None
    cost: Optional[Decimal] = None
    cost_currency: Optional[str] = None

    @field_validator("title")
    @classmethod
    def title_required(cls, v):
        if not v:
            raise ValueError("Say what the maintenance is for")
        return v

    @model_validator(mode="after")
    def done_needs_date(self):
        if self.status == "done" and not self.completed_date:
            raise ValueError("Give the date the work was done")
        return self


class VesselMaintenanceUpdate(_MaintenanceFields, _Reason):
    """Any change after logging — including marking it in progress or done."""
    maintenance_type: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    planned_date: Optional[date] = None
    completed_date: Optional[date] = None
    next_due_date: Optional[date] = None
    performed_by: Optional[str] = None
    cost: Optional[Decimal] = None
    cost_currency: Optional[str] = None
