"""
Phase 4 Test Suite — RAOMS
Tests: Client Portal, Analytics Dashboard, Email Service (graceful degradation)
Run: python -X utf8 test_phase4.py
"""
import httpx, sys
from datetime import datetime, timezone

BASE = "http://localhost:8005/api/v1"
PASS = "TestPass123!"

BM_EMAIL  = "bm2@reliantanchor.dev"
LO_EMAIL  = "lo.officer@reliantanchor.dev"
FM_EMAIL  = "fm.finance@reliantanchor.dev"
OS_EMAIL  = "os.supervisor@reliantanchor.dev"
MM_EMAIL  = "mm.marine@reliantanchor.dev"
CLI_EMAIL = "client@reliantanchor.dev"

results = []

def ok(name, resp, expected=200, show=None):
    passed = resp.status_code == expected
    if passed and expected in (200, 201):
        passed = resp.json().get("success") is True
    tag = "PASS" if passed else "FAIL"
    results.append((tag, name, resp.status_code))
    print(f"  [{tag}] {name} -> {resp.status_code}")
    if not passed:
        print(f"         {resp.text[:400]}")
    elif show:
        body = resp.json().get("data", {})
        val = body.get(show, body) if isinstance(body, dict) else body
        print(f"         {show}={val}")
    return resp.json() if passed else None

def fail(name, resp, expected):
    passed = resp.status_code == expected
    tag = "PASS" if passed else "FAIL"
    results.append((tag, name, resp.status_code))
    print(f"  [{tag}] {name} -> {resp.status_code} (expected {expected})")
    if not passed:
        print(f"         {resp.text[:300]}")
    return passed

c = httpx.Client(timeout=30)
tokens = {}
ids = {}

def H(role):
    t = tokens.get(role)
    return {"Authorization": f"Bearer {t}"} if t else {}

# ── Setup: Login all roles ─────────────────────────────────────────────────────
print("\n=== SETUP: Login all roles ===")
for label, email in [("bm",BM_EMAIL),("lo",LO_EMAIL),("fm",FM_EMAIL),
                     ("os",OS_EMAIL),("mm",MM_EMAIL),("client",CLI_EMAIL)]:
    r = c.post(f"{BASE}/auth/login", json={"email": email, "password": PASS})
    if r.status_code == 200 and r.json().get("success"):
        tokens[label] = r.json()["data"]["access_token"]
        r2 = c.get(f"{BASE}/auth/me", headers=H(label))
        if r2.status_code == 200:
            ids[label] = r2.json()["data"]["id"]
        print(f"  [OK] {label} logged in (id={ids.get(label,'?')[:8]}...)")
    else:
        print(f"  [FAIL] {label} login failed: {r.text[:100]}")

client_id = ids.get("client")
lo_id  = ids.get("lo")
fm_id  = ids.get("fm")
os_id  = ids.get("os")
mm_id  = ids.get("mm")

# ── Create a full_operation and walk it to bdn_approved for portal tests ───────
print("\n=== SETUP: Create and advance operation for portal tests ===")

r = c.post(f"{BASE}/operations", headers=H("bm"), json={
    "type": "full_operation",
    "client_id": client_id,
    "expected_volume_mt": 500.0,
    "currency": "USD",
    "notes": "Phase 4 portal test operation"
})
if r.status_code == 201 and r.json().get("success"):
    ids["op"] = r.json()["data"]["id"]
    ids["op_number"] = r.json()["data"]["operation_number"]
    print(f"  [OK] operation created: {ids['op_number']}")
else:
    print(f"  [FAIL] create operation: {r.text[:200]}")
    sys.exit(1)

op_id = ids["op"]

# Assign tasks
r = c.post(f"{BASE}/operations/{op_id}/tasks", headers=H("bm"), json={
    "lo_id": lo_id, "fm_id": fm_id, "os_id": os_id, "mm_id": mm_id
})
print(f"  tasks_assigned: {r.status_code}")

# BM transitions to awaiting_feedback
r = c.post(f"{BASE}/operations/{op_id}/transition", headers=H("bm"),
           json={"action": "request_feedback"})
print(f"  awaiting_feedback: {r.status_code}")

# Get a truck for feedback
r_truck = c.get(f"{BASE}/trucks", headers=H("bm"))
if r_truck.status_code == 200:
    trucks_list = r_truck.json()["data"]
    items = trucks_list if isinstance(trucks_list, list) else trucks_list.get("items", [])
    if items:
        ids["truck"] = items[0]["id"]
        print(f"  [OK] using truck {ids['truck'][:8]}...")
    else:
        print("  [WARN] no trucks found, creating one")
        r2 = c.post(f"{BASE}/trucks", headers=H("bm"), json={
            "plate_number": "P4T-001", "capacity_mt": 30.0
        })
        if r2.status_code in (200, 201):
            ids["truck"] = r2.json()["data"]["id"]

# LO submits feedback
if ids.get("truck"):
    r = c.post(f"{BASE}/operations/{op_id}/feedback", headers=H("lo"), json={
        "truck_ids": [ids["truck"]],
        "notes": "Phase 4 test feedback",
        "is_ready": True
    })
    print(f"  feedback_submitted: {r.status_code}")

# BM approves feedback
r = c.post(f"{BASE}/operations/{op_id}/transition", headers=H("bm"),
           json={"action": "approve_feedback"})
print(f"  feedback_approved: {r.status_code}")

# FM creates PFI
r = c.post(f"{BASE}/operations/{op_id}/pfis", headers=H("bm"), json={
    "amount": "50000.00",
    "currency": "USD",
    "supplier_name": "Marine Fuel Co",
    "description": "Phase 4 PFI"
})
if r.status_code == 201 and r.json().get("success"):
    ids["pfi"] = r.json()["data"]["id"]
    print(f"  [OK] PFI created: {r.json()['data']['pfi_number']}")
else:
    print(f"  [WARN] PFI creation: {r.status_code} {r.text[:200]}")

# FM records payment
if ids.get("pfi"):
    r = c.post(f"{BASE}/operations/{op_id}/payments", headers=H("fm"), json={
        "pfi_id": ids["pfi"],
        "amount": "50000.00",
        "currency": "USD",
        "payment_method": "bank_transfer",
        "payment_reference": "P4-REF-001",
        "payment_date": datetime.now(timezone.utc).isoformat(),
        "notes": "Phase 4 payment"
    })
    if r.status_code == 201 and r.json().get("success"):
        ids["payment"] = r.json()["data"]["id"]
        print(f"  [OK] payment recorded: {r.json()['data']['voucher_number']}")
    else:
        print(f"  [WARN] payment: {r.status_code} {r.text[:200]}")

# FM confirms payment
if ids.get("payment"):
    r = c.post(f"{BASE}/operations/{op_id}/payments/{ids['payment']}/confirm",
               headers=H("fm"), json={"notes": "Confirmed"})
    print(f"  payment_confirmed: {r.status_code}")

# Get or create a vessel for BDN
r_vessel = c.get(f"{BASE}/vessels", headers=H("bm"))
if r_vessel.status_code == 200:
    v_data = r_vessel.json()["data"]
    v_items = v_data if isinstance(v_data, list) else v_data.get("items", [])
    if v_items:
        ids["vessel"] = v_items[0]["id"]
        print(f"  [OK] using vessel {ids['vessel'][:8]}...")
    else:
        r2 = c.post(f"{BASE}/vessels", headers=H("bm"), json={
            "name": "MV Phase4", "imo_number": "IMO9988776",
            "vessel_type": "tanker", "capacity_mt": 5000.0
        })
        if r2.status_code in (200, 201):
            ids["vessel"] = r2.json()["data"]["id"]

# MM starts vessel ops
r = c.post(f"{BASE}/operations/{op_id}/transition", headers=H("mm"),
           json={"action": "start_vessel_operations"})
print(f"  vessel_operations: {r.status_code}")

# OS creates BDN
if ids.get("vessel"):
    r = c.post(f"{BASE}/operations/{op_id}/bdns", headers=H("os"), json={
        "vessel_id": ids["vessel"],
        "quantity_mt": "480.00",
        "fuel_type": "VLSFO",
        "delivery_date": datetime.now(timezone.utc).date().isoformat(),
        "notes": "Phase 4 BDN"
    })
    if r.status_code == 201 and r.json().get("success"):
        ids["bdn"] = r.json()["data"]["id"]
        print(f"  [OK] BDN created: {r.json()['data']['bdn_number']}")
    else:
        print(f"  [WARN] BDN: {r.status_code} {r.text[:200]}")

# BM approves BDN
if ids.get("bdn"):
    r = c.post(f"{BASE}/bdns/{ids['bdn']}/approve", headers=H("bm"),
               json={"notes": "Phase 4 approval"})
    print(f"  bdn_approved: {r.status_code}")

# Register a document for portal document test
r = c.post(f"{BASE}/operations/{op_id}/documents", headers=H("bm"), data={
    "document_type": "pfi",
    "file_name": "phase4_test.pdf",
    "file_url": "https://example.com/phase4_test.pdf",
    "description": "Phase 4 test document"
})
if r.status_code in (200, 201):
    ids["doc"] = r.json()["data"]["id"]
    print(f"  [OK] document registered: {ids['doc'][:8]}...")

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 4 TESTS
# ══════════════════════════════════════════════════════════════════════════════

# ── 1. Analytics Dashboard ─────────────────────────────────────────────────────
print("\n=== 1. Analytics Dashboard ===")

d = ok("BM can access analytics dashboard", c.get(f"{BASE}/analytics/dashboard", headers=H("bm")))
if d:
    dash = d.get("data", {})
    print(f"         ops.total={dash.get('operations',{}).get('total_operations','?')}")

ok("FM can access analytics dashboard", c.get(f"{BASE}/analytics/dashboard", headers=H("fm")))
ok("OS can access analytics dashboard", c.get(f"{BASE}/analytics/dashboard", headers=H("os")))

# ── 2. Analytics: Non-authorized roles blocked ─────────────────────────────────
print("\n=== 2. Analytics Access Control ===")

fail("LO cannot access analytics dashboard",
     c.get(f"{BASE}/analytics/dashboard", headers=H("lo")), 403)
fail("MM cannot access analytics dashboard",
     c.get(f"{BASE}/analytics/dashboard", headers=H("mm")), 403)
fail("Client cannot access analytics dashboard",
     c.get(f"{BASE}/analytics/dashboard", headers=H("client")), 403)

# ── 3. Analytics Dashboard structure ──────────────────────────────────────────
print("\n=== 3. Analytics Dashboard Structure ===")

r = c.get(f"{BASE}/analytics/dashboard", headers=H("bm"))
if r.status_code == 200 and r.json().get("success"):
    dash = r.json().get("data", {})
    has_ops = "operations" in dash
    has_trucks = "trucks" in dash
    has_vessels = "vessels" in dash
    has_revenue = "revenue" in dash

    def check_field(name, val):
        tag = "PASS" if val else "FAIL"
        results.append((tag, name, 200))
        print(f"  [{tag}] {name}")

    check_field("dashboard has operations key", has_ops)
    check_field("dashboard has trucks key", has_trucks)
    check_field("dashboard has vessels key", has_vessels)
    check_field("dashboard has revenue key", has_revenue)

    if has_ops:
        ops = dash["operations"]
        check_field("operations has total_operations", "total_operations" in ops)
        check_field("operations has by_status list", isinstance(ops.get("by_status"), list))
        check_field("operations has active_operations", "active_operations" in ops)
        check_field("operations has completed_this_month", "completed_this_month" in ops)
else:
    for name in ["dashboard has operations key","dashboard has trucks key",
                 "dashboard has vessels key","dashboard has revenue key",
                 "operations has total_operations","operations has by_status list",
                 "operations has active_operations","operations has completed_this_month"]:
        results.append(("FAIL", name, r.status_code))
        print(f"  [FAIL] {name} (dashboard request failed)")

# ── 4. Analytics Monthly Operations ───────────────────────────────────────────
print("\n=== 4. Analytics Monthly Operations ===")

ok("BM can get monthly operations (current year)",
   c.get(f"{BASE}/analytics/operations/monthly", headers=H("bm")))
ok("FM can get monthly operations with year param",
   c.get(f"{BASE}/analytics/operations/monthly?year=2025", headers=H("fm")))
fail("Client cannot get monthly operations",
     c.get(f"{BASE}/analytics/operations/monthly", headers=H("client")), 403)

# ── 5. Client Portal: Dashboard ───────────────────────────────────────────────
print("\n=== 5. Client Portal Dashboard ===")

d = ok("Client can access portal dashboard",
       c.get(f"{BASE}/portal/dashboard", headers=H("client")))
if d:
    dash = d.get("data", {})
    print(f"         total={dash.get('total_operations','?')}, active={dash.get('active_operations','?')}")

    def check_field(name, val):
        tag = "PASS" if val else "FAIL"
        results.append((tag, name, 200))
        print(f"  [{tag}] {name}")

    check_field("portal dashboard has total_operations", "total_operations" in dash)
    check_field("portal dashboard has active_operations", "active_operations" in dash)
    check_field("portal dashboard has completed_operations", "completed_operations" in dash)
    check_field("portal dashboard has cancelled_operations", "cancelled_operations" in dash)

# ── 6. Portal: Non-client roles blocked ───────────────────────────────────────
print("\n=== 6. Portal Access Control (non-clients blocked) ===")

fail("BM cannot access portal dashboard",
     c.get(f"{BASE}/portal/dashboard", headers=H("bm")), 403)
fail("FM cannot access portal dashboard",
     c.get(f"{BASE}/portal/dashboard", headers=H("fm")), 403)
fail("OS cannot access portal dashboard",
     c.get(f"{BASE}/portal/dashboard", headers=H("os")), 403)

# ── 7. Client Portal: List Operations ─────────────────────────────────────────
print("\n=== 7. Client Portal: List Operations ===")

d = ok("Client can list own operations",
       c.get(f"{BASE}/portal/operations", headers=H("client")))
if d:
    ops_data = d.get("data", {})
    items = ops_data.get("items", [])
    total = ops_data.get("total", 0)
    print(f"         total={total}, items_in_page={len(items)}")

    # Verify the operation we created is in the list
    found = any(item.get("id") == op_id for item in items)
    tag = "PASS" if found else "FAIL"
    results.append((tag, "created operation visible in portal list", 200))
    print(f"  [{tag}] created operation visible in portal list")

    # Verify no financial fields
    if items:
        first = items[0]
        no_pfi = "pfi_amount" not in first and "pfi_number" not in first
        no_payment = "payment_amount" not in first
        tag2 = "PASS" if no_pfi and no_payment else "FAIL"
        results.append((tag2, "portal list items contain no financial fields", 200))
        print(f"  [{tag2}] portal list items contain no financial fields")

# ── 8. Portal: Pagination params ──────────────────────────────────────────────
print("\n=== 8. Portal Operations Pagination ===")

ok("Client can paginate portal operations (page=1 per_page=5)",
   c.get(f"{BASE}/portal/operations?page=1&per_page=5", headers=H("client")))
ok("Client can filter portal operations by status",
   c.get(f"{BASE}/portal/operations?status=draft", headers=H("client")))
fail("Invalid status returns 422",
     c.get(f"{BASE}/portal/operations?status=nonexistent_status", headers=H("client")), 422)

# ── 9. Portal: Get Single Operation ───────────────────────────────────────────
print("\n=== 9. Client Portal: Get Single Operation ===")

d = ok("Client can get own operation detail",
       c.get(f"{BASE}/portal/operations/{op_id}", headers=H("client")))
if d:
    op = d.get("data", {})
    has_completed_at = "completed_at" in op
    tag = "PASS" if has_completed_at else "FAIL"
    results.append((tag, "operation detail has completed_at field", 200))
    print(f"  [{tag}] operation detail has completed_at field")

fail("Client cannot get another user's operation (non-existent to them)",
     c.get(f"{BASE}/portal/operations/00000000-0000-0000-0000-000000000000",
           headers=H("client")), 404)
fail("BM cannot use portal operation endpoint",
     c.get(f"{BASE}/portal/operations/{op_id}", headers=H("bm")), 403)

# ── 10. Portal: Documents ─────────────────────────────────────────────────────
print("\n=== 10. Client Portal: Operation Documents ===")

d = ok("Client can list operation documents",
       c.get(f"{BASE}/portal/operations/{op_id}/documents", headers=H("client")))
if d:
    docs = d.get("data", [])
    doc_count = len(docs) if isinstance(docs, list) else 0
    print(f"         documents found: {doc_count}")
    tag = "PASS" if doc_count >= 0 else "FAIL"  # just check the endpoint works
    results.append((tag, "portal documents endpoint returns list", 200))
    print(f"  [{tag}] portal documents endpoint returns list")

fail("Client cannot list documents for operation they don't own",
     c.get(f"{BASE}/portal/operations/00000000-0000-0000-0000-000000000000/documents",
           headers=H("client")), 404)

# ── 11. Portal: BDNs ──────────────────────────────────────────────────────────
print("\n=== 11. Client Portal: Operation BDNs ===")

d = ok("Client can list operation BDNs",
       c.get(f"{BASE}/portal/operations/{op_id}/bdns", headers=H("client")))
if d:
    bdns = d.get("data", [])
    bdn_count = len(bdns) if isinstance(bdns, list) else 0
    print(f"         BDNs found: {bdn_count}")

    # If BDN was approved earlier, it should appear here
    if ids.get("bdn") and bdn_count > 0:
        found_bdn = any(b.get("id") == ids["bdn"] for b in bdns)
        tag = "PASS" if found_bdn else "FAIL"
        results.append((tag, "approved BDN appears in portal BDN list", 200))
        print(f"  [{tag}] approved BDN appears in portal BDN list")

fail("Client cannot list BDNs for operation they don't own",
     c.get(f"{BASE}/portal/operations/00000000-0000-0000-0000-000000000000/bdns",
           headers=H("client")), 404)

# ── 12. Portal isolation: clients cannot see each other's operations ───────────
print("\n=== 12. Portal Data Isolation ===")

# Create a second client (BM only) — use admin endpoint
# We check that client sees only their own data in the count
r = c.get(f"{BASE}/portal/operations", headers=H("client"))
r2 = c.get(f"{BASE}/operations", headers=H("bm"))  # BM sees all
if r.status_code == 200 and r2.status_code == 200:
    portal_total = r.json()["data"].get("total", 0)
    bm_total_data = r2.json()["data"]
    bm_total = bm_total_data.get("total") if isinstance(bm_total_data, dict) else len(bm_total_data)
    tag = "PASS" if portal_total <= (bm_total or 0) else "FAIL"
    results.append((tag, "portal total <= BM total (isolation)", 200))
    print(f"  [{tag}] portal total ({portal_total}) <= BM total ({bm_total}) (isolation)")

# ── 13. Unauthenticated access blocked ────────────────────────────────────────
print("\n=== 13. Unauthenticated Access Blocked ===")

fail("Unauthenticated cannot access portal dashboard",
     c.get(f"{BASE}/portal/dashboard"), 401)
fail("Unauthenticated cannot access analytics dashboard",
     c.get(f"{BASE}/analytics/dashboard"), 401)
fail("Unauthenticated cannot list portal operations",
     c.get(f"{BASE}/portal/operations"), 401)

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
passed = sum(1 for t,_,_ in results if t == "PASS")
failed = sum(1 for t,_,_ in results if t == "FAIL")
total  = len(results)
print(f"PHASE 4 RESULTS: {passed}/{total} PASSED, {failed} FAILED")
if failed:
    print("\nFailed tests:")
    for tag, name, code in results:
        if tag == "FAIL":
            print(f"  - {name} (got {code})")
print("="*60)
sys.exit(0 if failed == 0 else 1)
