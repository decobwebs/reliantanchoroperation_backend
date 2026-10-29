"""Tests for truck availability and duplicate-plate matching.

Pure logic — no database, no network.

Run:  cd reliant-anchor-api && ./venv/Scripts/python.exe -m pytest tests -q
"""
from dataclasses import dataclass
from uuid import uuid4

import pytest

from app.models.enums import TruckStatus
from app.services.truck_availability import (
    LiveAssignment, availability, clean_plate, plate_key,
)


@dataclass
class FakeTruck:
    status: TruckStatus


def _on_job(status: TruckStatus) -> LiveAssignment:
    return LiveAssignment(status=status, operation_id=uuid4(), operation_number="RA-2026-0001")


# ── plates: the ten real duplicate pairs found on 29 Oct 2026 ──────────────

@pytest.mark.parametrize("a, b", [
    ("⁠DMS 117 XA", "DMS117XA"),
    ("NCH897XA", "NCH897XA."),
    ("KAF830XA", "KAF 830 XA"),
    ("ABC979ZU.", "ABC979ZU"),
    ("mka 31 zb", "MKA31ZB"),
])
def test_same_truck_same_key(a, b):
    assert plate_key(a) == plate_key(b)


def test_different_trucks_differ():
    assert plate_key("DKA63YN") != plate_key("DKA63YM")


def test_clean_plate_keeps_inner_spacing_drops_junk():
    assert clean_plate("⁠ DMS 117 XA. ") == "DMS 117 XA"
    assert clean_plate("NCH897XA.") == "NCH897XA"


def test_merged_suffix_never_matches_live_plate():
    # A retired duplicate is renamed "<plate> (merged)" so it cannot clash.
    assert plate_key("ABC979ZU (merged)") != plate_key("ABC979ZU")


# ── availability ───────────────────────────────────────────────────────────

def test_free_truck_is_available():
    assert availability(FakeTruck(TruckStatus.available), None) == TruckStatus.available


def test_job_sets_status():
    t = FakeTruck(TruckStatus.available)
    assert availability(t, _on_job(TruckStatus.in_transit)) == TruckStatus.in_transit


@pytest.mark.parametrize("manual", [TruckStatus.maintenance, TruckStatus.out_of_service])
def test_hand_set_state_beats_job(manual):
    assert availability(FakeTruck(manual), _on_job(TruckStatus.discharging)) == manual


def test_stale_stored_status_is_ignored():
    # A truck stored as "assigned" with no live job is available — the stored
    # value is only trusted for the two hand-set states.
    assert availability(FakeTruck(TruckStatus.assigned), None) == TruckStatus.available
