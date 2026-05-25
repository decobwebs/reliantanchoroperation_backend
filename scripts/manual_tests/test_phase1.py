"""
Phase 1 Test Suite — RAOMS (idempotent)
Run: python -X utf8 test_phase1.py
Reuses existing test users if they already exist.
"""
import httpx, sys

BASE = "http://localhost:8001/api/v1"
PASS = "TestPass123!"

# Fixed test credentials — safe to re-use across runs
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
        print(f"         {resp.text[:300]}")
    elif show:
        body = resp.json().get("data", {})
        if isinstance(body, dict):
            print(f"         {show}={body.get(show)}")
    return resp.json() if passed else None

def fail(name, resp, expected):
    passed = resp.status_code == expected
    tag = "PASS" if passed else "FAIL"
    results.append((tag, name, resp.status_code))
    print(f"  [{tag}] {name} -> {resp.status_code} (expected {expected})")
    if not passed:
        print(f"         {resp.text[:200]}")
    return passed

c = httpx.Client(timeout=30)
tokens = {}
ids = {}

def H(role):
    t = tokens.get(role)
    return {"Authorization": f"Bearer {t}"} if t else {}

def ensure_login(label, email):
    """Login, return True if successful."""
    r = c.post(f"{BASE}/auth/login", json={"email": email, "password": PASS})
    if r.status_code == 200 and r.json().get("success"):
        d = r.json()["data"]
        tokens[label] = d["access_token"]
        tokens[f"{label}_refresh"] = d.get("refresh_token")
        return True
    return False

def ensure_user(label, email, role, creator="bm"):
    """Login to existing user, or create via admin if not exists."""
    if ensure_login(label, email):
        results.append(("PASS", f"Login existing {label}", 200))
        print(f"  [PASS] Login existing {label} -> 200")
        # Get user ID
        r = c.get(f"{BASE}/auth/me", headers=H(label))
        if r.status_code == 200:
            ids[label] = r.json()["data"]["id"]
        return True

    # User doesn't exist yet — create via admin
    r = c.post(f"{BASE}/admin/users", headers=H(creator), json={
        "email": email, "password": PASS,
        "full_name": f"Test {label.upper()}", "role": role
    })
    if r.status_code == 201 and r.json().get("success"):
        ids[label] = r.json()["data"]["id"]
        results.append(("PASS", f"Create {label}", 201))
        print(f"  [PASS] Create {label} -> 201 (role={r.json()['data']['role']})")
        ensure_login(label, email)
        return True

    results.append(("FAIL", f"Ensure {label}", r.status_code))
    print(f"  [FAIL] Ensure {label} -> {r.status_code}: {r.text[:200]}")
    return False

# ============================================================
print("\n=== 1. HEALTH ===")
ok("GET /health", c.get(f"{BASE}/health"))

# ============================================================
print("\n=== 2. BOOTSTRAP / LOGIN BM ===")

# Try login first (idempotent)
if not ensure_login("bm", BM_EMAIL):
    # No BM yet — use bootstrap
    r = c.post(f"{BASE}/auth/bootstrap", json={
        "email": BM_EMAIL, "password": PASS,
        "full_name": "Ibrahim Bunker Manager"
    })
    data = ok("POST /auth/bootstrap (first BM)", r, 201, show="role")
    if data:
        ids["bm"] = data["data"]["id"]
        ensure_login("bm", BM_EMAIL)
else:
    results.append(("PASS", "BM login (existing)", 200))
    print(f"  [PASS] BM already exists, logged in")

if not tokens.get("bm"):
    print("  [FATAL] Cannot obtain BM token — aborting tests")
    sys.exit(1)

# Verify BM token returns correct role
r = c.get(f"{BASE}/auth/me", headers=H("bm"))
if r.status_code == 200:
    bm_role = r.json()["data"]["role"]
    ids["bm"] = r.json()["data"]["id"]
    if bm_role == "bunker_manager":
        results.append(("PASS", "BM has correct role", 200))
        print(f"  [PASS] BM role verified: {bm_role}")
    else:
        results.append(("FAIL", f"BM role is {bm_role}", 200))
        print(f"  [FAIL] BM role is '{bm_role}', expected 'bunker_manager'")

# Bootstrap lock test
r = c.post(f"{BASE}/auth/bootstrap", json={
    "email": "another@reliantanchor.dev", "password": PASS, "full_name": "Extra BM"
})
fail("POST /auth/bootstrap (locked after first BM) -> 403", r, 403)

# ============================================================
print("\n=== 3. ENSURE ALL TEAM USERS ===")
ensure_user("lo", LO_EMAIL, "logistics_officer")
ensure_user("fm", FM_EMAIL, "finance_manager")
ensure_user("os", OS_EMAIL, "ops_supervisor")
ensure_user("mm", MM_EMAIL, "marine_manager")

# Client via public register (or login if exists)
if not ensure_login("client", CLI_EMAIL):
    r = c.post(f"{BASE}/auth/register", json={
        "email": CLI_EMAIL, "password": PASS, "full_name": "Test Client"
    })
    data = ok("POST /auth/register (client)", r, 201, show="role")
    if data:
        ids["client"] = data["data"]["id"]
        ensure_login("client", CLI_EMAIL)
else:
    r = c.get(f"{BASE}/auth/me", headers=H("client"))
    if r.status_code == 200:
        ids["client"] = r.json()["data"]["id"]
    results.append(("PASS", "client login (existing)", 200))
    print("  [PASS] client already exists, logged in")

# ============================================================
print("\n=== 4. AUTH/ME ===")
r = c.get(f"{BASE}/auth/me", headers=H("bm"))
ok("GET /auth/me (BM)", r, 200, show="role")

fail("GET /auth/me (no token) -> 401", c.get(f"{BASE}/auth/me"), 401)

# ============================================================
print("\n=== 5. UPDATE PROFILE ===")
r = c.put(f"{BASE}/auth/me", headers=H("bm"), json={"full_name": "Ibrahim A. Manager"})
ok("PUT /auth/me (BM)", r, 200, show="full_name")

# ============================================================
print("\n=== 6. OPERATIONS CRUD ===")
client_id = ids.get("client")
op_id = None

if client_id:
    r = c.post(f"{BASE}/operations", headers=H("bm"), json={
        "type": "full_operation",
        "client_id": client_id,
        "expected_volume_mt": 500.0,
        "currency": "NGN",
        "notes": "Phase 1 integration test"
    })
    data = ok("POST /operations (BM)", r, 201)
    if data:
        op_id = data["data"]["id"]
        print(f"         {data['data']['operation_number']} | status={data['data']['status']}")

    if tokens.get("lo"):
        r = c.post(f"{BASE}/operations", headers=H("lo"), json={
            "type": "truck_only", "client_id": client_id, "expected_volume_mt": 100.0, "currency": "NGN"
        })
        fail("POST /operations (LO) -> 403", r, 403)

r = c.get(f"{BASE}/operations", headers=H("bm"))
data = ok("GET /operations (BM)", r, 200)
if data:
    print(f"         total={data['data'].get('total')}")

fail("GET /operations (no token) -> 401", c.get(f"{BASE}/operations"), 401)

if op_id:
    ok("GET /operations/:id", c.get(f"{BASE}/operations/{op_id}", headers=H("bm")), 200, show="status")
    ok("PUT /operations/:id", c.put(f"{BASE}/operations/{op_id}", headers=H("bm"), json={"notes": "Updated"}), 200)

    if tokens.get("lo"):
        fail("PUT /operations/:id (LO) -> 403", c.put(f"{BASE}/operations/{op_id}", headers=H("lo"), json={"notes": "x"}), 403)

    r = c.get(f"{BASE}/operations/{op_id}/timeline", headers=H("bm"))
    data = ok("GET /operations/:id/timeline", r, 200)
    if data:
        print(f"         entries={len(data['data'])}")

# ============================================================
print("\n=== 7. STATE MACHINE ===")

if op_id:
    r = c.post(f"{BASE}/operations/{op_id}/transition", headers=H("bm"), json={
        "to_status": "tasks_assigned", "reason": "Assigning team"
    })
    ok("Transition: draft -> tasks_assigned", r, 200, show="status")

    fail("Transition: skip steps -> 422",
         c.post(f"{BASE}/operations/{op_id}/transition", headers=H("bm"), json={"to_status": "bdn_pending"}), 422)

    if tokens.get("lo"):
        fail("Transition: wrong role -> 422",
             c.post(f"{BASE}/operations/{op_id}/transition", headers=H("lo"), json={"to_status": "pfi_linked"}), 422)

    ok("POST pause (BM)",
       c.post(f"{BASE}/operations/{op_id}/pause", headers=H("bm"), json={"reason": "Waiting for clearance"}), 200, show="paused_reason")

    if tokens.get("lo"):
        fail("POST pause (LO) -> 403",
             c.post(f"{BASE}/operations/{op_id}/pause", headers=H("lo"), json={"reason": "x"}), 403)

    ok("POST resume (BM)", c.post(f"{BASE}/operations/{op_id}/resume", headers=H("bm")), 200)

# ============================================================
print("\n=== 8. SOFT DELETE ===")

if client_id:
    r = c.post(f"{BASE}/operations", headers=H("bm"), json={
        "type": "truck_only", "client_id": client_id, "expected_volume_mt": 10.0, "currency": "USD"
    })
    data = ok("POST /operations (delete target)", r, 201)
    if data:
        del_id = data["data"]["id"]
        ok("DELETE /operations/:id (draft)", c.delete(f"{BASE}/operations/{del_id}", headers=H("bm")), 200)
        fail("DELETE /operations/:id (deleted) -> 404", c.delete(f"{BASE}/operations/{del_id}", headers=H("bm")), 404)

# ============================================================
print("\n=== 9. ADMIN ===")

r = c.get(f"{BASE}/admin/users", headers=H("bm"))
data = ok("GET /admin/users (BM)", r, 200)
if data:
    print(f"         total={data['data']['total']}")

if tokens.get("lo"):
    fail("GET /admin/users (LO) -> 403", c.get(f"{BASE}/admin/users", headers=H("lo")), 403)

r = c.get(f"{BASE}/admin/audit-logs", headers=H("bm"))
data = ok("GET /admin/audit-logs (BM)", r, 200)
if data:
    print(f"         audit entries={data['data'].get('total')}")

r = c.get(f"{BASE}/admin/settings", headers=H("bm"))
data = ok("GET /admin/settings (BM)", r, 200)
if data:
    print(f"         keys={[s['key'] for s in data.get('data', [])]}")

# ============================================================
print("\n=== 10. TOKEN REFRESH ===")
if tokens.get("bm_refresh"):
    ok("POST /auth/refresh (BM)", c.post(f"{BASE}/auth/refresh", json={"refresh_token": tokens["bm_refresh"]}), 200)

# ============================================================
print("\n=== 11. RBAC ISOLATION ===")

if tokens.get("client"):
    r = c.get(f"{BASE}/operations", headers=H("client"))
    data = ok("GET /operations (client - no assignments)", r, 200)
    if data:
        print(f"         client sees {data['data'].get('total', 0)} ops")

if tokens.get("fm"):
    fail("GET /admin/users (FM) -> 403", c.get(f"{BASE}/admin/users", headers=H("fm")), 403)

# ============================================================
print("\n" + "=" * 52)
passed = sum(1 for r in results if r[0] == "PASS")
failed = sum(1 for r in results if r[0] == "FAIL")
print(f"  PASSED : {passed}")
print(f"  FAILED : {failed}")
print(f"  TOTAL  : {len(results)}")
if failed:
    print("\n  Failed:")
    for r in results:
        if r[0] == "FAIL":
            print(f"    x {r[1]}")
    sys.exit(1)
else:
    print("\n  All Phase 1 tests passed.")
