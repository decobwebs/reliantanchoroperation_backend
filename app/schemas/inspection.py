from datetime import date
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, field_validator, model_validator

from app.models.inspection import INSPECTION_SUBJECTS, INSPECTION_RESULTS
from app.schemas.vessel_record import _Reason, _one_of, _strip


class InspectionActionCreate(BaseModel):
    description: str
    owner_id: UUID
    due_date: date

    @field_validator("description", mode="before")
    @classmethod
    def strip_text(cls, v):
        return _strip(v)

    @field_validator("description")
    @classmethod
    def description_required(cls, v):
        if not v:
            raise ValueError("Say what needs to be fixed")
        return v


class InspectionCreate(BaseModel):
    subject_type: str
    # Exactly one of these, matching subject_type. An operation is given by
    # its number, the way it appears on the paperwork.
    vessel_id: Optional[UUID] = None
    truck_id: Optional[UUID] = None
    operation_number: Optional[str] = None
    site_name: Optional[str] = None
    inspected_on: date
    title: str
    result: str
    findings: Optional[str] = None
    actions: List[InspectionActionCreate] = []

    @field_validator("operation_number", "site_name", "title", "findings", mode="before")
    @classmethod
    def strip_text(cls, v):
        return _strip(v)

    @field_validator("title")
    @classmethod
    def title_required(cls, v):
        if not v:
            raise ValueError("Say what was inspected")
        return v

    @field_validator("subject_type")
    @classmethod
    def valid_subject(cls, v):
        return _one_of(v, INSPECTION_SUBJECTS, "subject")

    @field_validator("result")
    @classmethod
    def valid_result(cls, v):
        return _one_of(v, INSPECTION_RESULTS, "result")

    @model_validator(mode="after")
    def subject_and_findings(self):
        given = {
            "vessel": self.vessel_id, "truck": self.truck_id,
            "operation": self.operation_number, "site": self.site_name,
        }
        if not given[self.subject_type]:
            raise ValueError(f"Choose the {self.subject_type} that was inspected")
        for kind, value in given.items():
            if kind != self.subject_type and value:
                raise ValueError(f"An inspection of a {self.subject_type} cannot also name a {kind}")
        if self.result != "pass" and not self.findings:
            raise ValueError("Write down what was found")
        return self


class InspectionUpdate(_Reason):
    inspected_on: Optional[date] = None
    title: Optional[str] = None
    result: Optional[str] = None
    findings: Optional[str] = None
    site_name: Optional[str] = None

    @field_validator("title", "findings", "site_name", mode="before")
    @classmethod
    def strip_text(cls, v):
        return _strip(v)

    @field_validator("result")
    @classmethod
    def valid_result(cls, v):
        return _one_of(v, INSPECTION_RESULTS, "result")


class InspectionActionUpdate(_Reason):
    description: Optional[str] = None
    owner_id: Optional[UUID] = None
    due_date: Optional[date] = None

    @field_validator("description", mode="before")
    @classmethod
    def strip_text(cls, v):
        return _strip(v)


class InspectionActionComplete(BaseModel):
    note: str

    @field_validator("note", mode="before")
    @classmethod
    def strip_note(cls, v):
        return _strip(v)

    @field_validator("note")
    @classmethod
    def note_required(cls, v):
        if not v:
            raise ValueError("Say what was done to fix it")
        return v


class InspectionActionReopen(_Reason):
    pass
