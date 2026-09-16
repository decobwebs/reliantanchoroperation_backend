from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class MetricOut(BaseModel):
    """One measured metric on a scorecard.

    `value` and `score` are both optional and both meaningful when absent:
    None means the operation produced nothing to measure, which the UI must
    render blank — never a dash, never a zero. `no_data_reason` says why, so
    a person reading a blank cell knows whether the work is outstanding or
    simply not applicable to this kind of operation.
    """
    key: str
    label: str
    unit: str
    value: Optional[float] = None
    score: Optional[float] = None
    rating: Optional[str] = None
    target: Optional[float] = None
    fail: Optional[float] = None
    weight: float
    critical: bool = False
    source: str = "auto"
    doc: str = ""            # which job document this target came from
    description: str = ""
    sample_size: Optional[int] = None   # how many records the value averages over
    no_data_reason: Optional[str] = None
    target_overridden: bool = False     # a kpi_targets row is in effect


class RoleScoreOut(BaseModel):
    role: str
    role_label: str
    score: Optional[float] = None
    rating: Optional[str] = None
    metrics: List[MetricOut] = Field(default_factory=list)


class ContributorOut(BaseModel):
    """A person whose work shows up on this operation, and where."""
    user_id: UUID
    full_name: Optional[str] = None
    role: str
    role_label: str
    involvement: List[str] = Field(default_factory=list)


class OperationScorecardOut(BaseModel):
    operation_id: UUID
    operation_number: Optional[str] = None
    operation_type: Optional[str] = None
    status: Optional[str] = None
    computed_at: datetime
    overall_score: Optional[float] = None
    overall_rating: Optional[str] = None
    roles: List[RoleScoreOut] = Field(default_factory=list)
    contributors: List[ContributorOut] = Field(default_factory=list)


class KpiTargetOut(BaseModel):
    metric_key: str
    label: str
    unit: str
    role: str
    role_label: str
    curve: str
    weight: float
    critical: bool
    doc: str
    description: str
    # What is actually in force right now, after any override.
    effective_target: Optional[float] = None
    effective_fail: Optional[float] = None
    # The catalog defaults, so the BM can see what they moved away from.
    default_target: Optional[float] = None
    default_fail: Optional[float] = None
    is_overridden: bool = False
    overridden_at: Optional[datetime] = None
    overridden_by: Optional[str] = None
    override_reason: Optional[str] = None


class KpiTargetSetIn(BaseModel):
    metric_key: str
    target_value: Optional[float] = None
    fail_value: Optional[float] = None
    weight: Optional[float] = None
    is_active: Optional[bool] = None
    effective_from: Optional[datetime] = None
    # Same reason-gated convention as every other corrective action here.
    reason: str = Field(min_length=10)
