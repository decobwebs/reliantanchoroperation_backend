"""Response shapes for per-person KPI (Phase 2).

Phase 1's `schemas.py` is frozen and Phase 3 has its own — see §7b of
KPI-WORKLOG.md.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class MyMetric(BaseModel):
    """One line of a person's score, with everything needed to argue with it.

    `attribution` says which records produced the number ("Trucks you
    recorded"), and `doc` quotes the job document the target came from. A
    score nobody can trace is a score nobody trusts.
    """
    key: str
    label: str
    unit: str
    value: Optional[float] = None
    score: Optional[float] = None
    rating: Optional[str] = None
    target: Optional[float] = None
    weight: float
    critical: bool = False
    sample_size: Optional[int] = None
    attribution: Optional[str] = None
    doc: str = ""
    no_data_reason: Optional[str] = None


class MyScorecard(BaseModel):
    period: str
    user_id: str
    full_name: Optional[str] = None
    role: str
    role_label: str
    score: Optional[float] = None
    rating: Optional[str] = None
    metrics_measured: int = 0
    metrics_total: int = 0
    # Share of the role's metric weight that actually had something recorded.
    # A score is withheld below UserKpiService.MIN_COVERAGE so that one lucky
    # measurement cannot outrank six real ones.
    coverage: float = 0.0
    insufficient_data: bool = False
    metrics: List[MyMetric] = Field(default_factory=list)

    # Deliberately a median of the person's own role, and deliberately
    # anonymous. The BM sees names on the leaderboard; a staff member sees
    # only where they stand, never a colleague's number.
    team_median: Optional[float] = None
    team_size: int = 0
    # True when this month is still running and the figures will keep moving.
    provisional: bool = True


class LeaderboardRow(BaseModel):
    user_id: str
    full_name: Optional[str] = None
    role: str
    role_label: str
    score: Optional[float] = None
    rating: Optional[str] = None
    metrics_measured: int = 0
    metrics_total: int = 0
    coverage: float = 0.0
    insufficient_data: bool = False


class Leaderboard(BaseModel):
    """Named, and Bunker-Manager-only. See the router for why."""
    period: str
    provisional: bool = True
    rows: List[LeaderboardRow] = Field(default_factory=list)
    team_median: Optional[float] = None
    scored_count: int = 0
    unscored_count: int = 0
