from typing import Optional
from datetime import datetime
from uuid import UUID
from decimal import Decimal
from pydantic import BaseModel, field_validator


class TerminalLoadingReceiptCreate(BaseModel):
    """Lean intake reading for terminal-sourced loading — quantity + GOV/GSV/
    MT only, no density/temperature (decision 11). GOV/GSV/MT are optional,
    same reasoning as the Vessel BDN's received_* block: not every submitter
    will have all three at hand."""
    quantity_litres: Decimal
    gov: Optional[Decimal] = None
    gsv: Optional[Decimal] = None
    mt_vacuum: Optional[Decimal] = None
    description: Optional[str] = None

    @field_validator("description", mode="before")
    @classmethod
    def strip_description(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() if v else v

    @field_validator("quantity_litres")
    @classmethod
    def positive_quantity(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Must be greater than zero")
        return v

    @field_validator("gov", "gsv", "mt_vacuum")
    @classmethod
    def positive_if_given(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        if v is not None and v <= 0:
            raise ValueError("Must be greater than zero")
        return v


class TerminalLoadingReceiptOut(BaseModel):
    id: UUID
    operation_id: UUID
    quantity_litres: Decimal
    gov: Optional[Decimal] = None
    gsv: Optional[Decimal] = None
    mt_vacuum: Optional[Decimal] = None
    description: Optional[str] = None
    recorded_by: UUID
    recorded_by_name: Optional[str] = None
    recorded_at: datetime

    model_config = {"from_attributes": True}


class QuantitySummaryOut(BaseModel):
    """Running Total Loaded Quantity for the operation — trucks (MT) +
    terminal receipts (MT, via mt_vacuum) combined into one figure. Entries
    without a computed MT figure yet (mt_vacuum not recorded) don't
    contribute, same as any other not-yet-measured reading."""
    truck_loaded_mt: Decimal
    terminal_loaded_mt: Decimal
    total_loaded_mt: Decimal
