from typing import List, Optional
from datetime import date, datetime
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, field_validator


# ── PPDL ─────────────────────────────────────────────────────────────────────

class PpdlProductCreate(BaseModel):
    product_type: str
    quantity_litres: Decimal

    @field_validator("product_type", mode="before")
    @classmethod
    def strip_product(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("quantity_litres")
    @classmethod
    def positive_quantity(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v


class PpdlCreate(BaseModel):
    ppdl_number: str
    issue_date: date
    expiry_date: date
    products: List[PpdlProductCreate]

    @field_validator("ppdl_number", mode="before")
    @classmethod
    def strip_number(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("products")
    @classmethod
    def at_least_one_product(cls, v: List[PpdlProductCreate]) -> List[PpdlProductCreate]:
        if not v:
            raise ValueError("At least one product is required")
        return v


class PpdlProductQuantityUpdate(BaseModel):
    quantity_litres: Decimal
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to change a PPDL product quantity")
        return v

    @field_validator("quantity_litres")
    @classmethod
    def positive_quantity(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v


class PpdlProductOut(BaseModel):
    id: UUID
    ppdl_id: UUID
    product_type: str
    quantity_litres: Decimal
    allocated_litres: Decimal = Decimal("0")
    remaining_litres: Optional[Decimal] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PpdlOut(BaseModel):
    id: UUID
    ppdl_number: str
    issue_date: date
    expiry_date: date
    is_current: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    products: List[PpdlProductOut] = []

    model_config = {"from_attributes": True}


# ── BFL ──────────────────────────────────────────────────────────────────────

class BflCreate(BaseModel):
    bfl_number: str
    product_type: str
    quantity_litres: Decimal
    vessel: Optional[str] = None
    expiry_date: date

    @field_validator("bfl_number", "product_type", "vessel", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("quantity_litres")
    @classmethod
    def positive_quantity(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v


class BflUpdate(BaseModel):
    quantity_litres: Optional[Decimal] = None
    vessel: Optional[str] = None
    expiry_date: Optional[date] = None
    reason: str

    @field_validator("vessel", "reason", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to edit a BFL")
        return v

    @field_validator("quantity_litres")
    @classmethod
    def positive_quantity(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v


class BflDeactivateRequest(BaseModel):
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required to deactivate a BFL")
        return v


class BflOut(BaseModel):
    id: UUID
    bfl_number: str
    ppdl_id: UUID
    ppdl_number: Optional[str] = None
    product_type: str
    quantity_litres: Decimal
    allocated_litres: Decimal = Decimal("0")
    remaining_litres: Optional[Decimal] = None
    vessel: Optional[str] = None
    expiry_date: date
    is_active: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Naval Clearance ──────────────────────────────────────────────────────────

class NavalClearanceDrawdownCreate(BaseModel):
    bfl_id: UUID
    quantity_litres: Decimal

    @field_validator("quantity_litres")
    @classmethod
    def positive_quantity(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Quantity must be greater than zero")
        return v


class NavalClearanceVesselCreate(BaseModel):
    client_id: UUID
    vessel_name: str
    imo_number: Optional[str] = None

    @field_validator("vessel_name", "imo_number", mode="before")
    @classmethod
    def strip_strings(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("vessel_name")
    @classmethod
    def vessel_name_required(cls, v: str) -> str:
        if not v:
            raise ValueError("Vessel name is required")
        return v


class NavalClearanceCreate(BaseModel):
    clearance_number: str
    date_of_loading: date
    expiry_date: date
    drawdowns: List[NavalClearanceDrawdownCreate]
    loading_locations: List[str]
    vessels: List[NavalClearanceVesselCreate]

    @field_validator("clearance_number", mode="before")
    @classmethod
    def strip_number(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("loading_locations", mode="before")
    @classmethod
    def strip_locations(cls, v: List[str]) -> List[str]:
        return [loc.strip() for loc in v if loc and loc.strip()] if v else v

    @field_validator("drawdowns")
    @classmethod
    def at_least_one_drawdown(cls, v: List[NavalClearanceDrawdownCreate]) -> List[NavalClearanceDrawdownCreate]:
        if not v:
            raise ValueError("At least one BFL drawdown is required")
        return v

    @field_validator("loading_locations")
    @classmethod
    def at_least_one_location(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("At least one loading location is required")
        return v

    @field_validator("vessels")
    @classmethod
    def at_least_one_vessel(cls, v: List[NavalClearanceVesselCreate]) -> List[NavalClearanceVesselCreate]:
        if not v:
            raise ValueError("At least one client vessel is required")
        return v


class NavalClearanceLoadingLocationAdd(BaseModel):
    location: str

    @field_validator("location", mode="before")
    @classmethod
    def strip_location(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("location")
    @classmethod
    def location_required(cls, v: str) -> str:
        if not v:
            raise ValueError("Location is required")
        return v


class NavalClearanceRemoveWithReason(BaseModel):
    reason: str

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, v: str) -> str:
        return v.strip() if v else v

    @field_validator("reason")
    @classmethod
    def reason_required(cls, v: str) -> str:
        if not v:
            raise ValueError("A reason is required")
        return v


class NavalClearanceDrawdownOut(BaseModel):
    id: UUID
    naval_clearance_id: UUID
    bfl_id: UUID
    bfl_number: Optional[str] = None
    product_type: Optional[str] = None
    quantity_litres: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}


class NavalClearanceLoadingLocationOut(BaseModel):
    id: UUID
    location: str
    sort_order: int

    model_config = {"from_attributes": True}


class NavalClearanceVesselOut(BaseModel):
    id: UUID
    client_id: UUID
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    vessel_name: str
    imo_number: Optional[str] = None
    current_eta: Optional[datetime] = None

    model_config = {"from_attributes": True}


class NavalClearanceOut(BaseModel):
    id: UUID
    clearance_number: str
    date_of_loading: date
    expiry_date: date
    is_valid: bool = True
    document_url: Optional[str] = None
    ppdl_number: Optional[str] = None
    bfl_numbers: List[str] = []
    products: List[str] = []
    total_quantity_litres: Decimal = Decimal("0")
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    drawdowns: List[NavalClearanceDrawdownOut] = []
    loading_locations: List[NavalClearanceLoadingLocationOut] = []
    vessels: List[NavalClearanceVesselOut] = []

    model_config = {"from_attributes": True}
