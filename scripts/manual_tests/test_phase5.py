"""
Phase 5 Test Suite — RAOMS Hardening
Tests: Request ID middleware, rate limiting, client milestones,
       audit log endpoint, startup config validation.
Run: python -X utf8 test_phase5.py
"""
import httpx, sys, time

BASE = "http://localhost:8006/api/v1"
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

def check(name, condition, detail=""):
    tag = "PASS" if condition else "FAIL"
    results.append((tag, name, "-"))
    print(f"  [{tag}] {name}" + (f" — {detail}" if detail else ""))
    return condition

c = httpx.Client(timeout=30)
tokens = {}
ids = {}

def H(role):
    t = tokens.get(role)
    return {"Authorization": f"Bearer {t}"} if t else {}

# ── Setup: Login ───────────────────────────────────────────────────────────────
print("\n=== SETUP: Login all roles ===")
for label, email in [("bm",BM_EMAIL),("lo",LO_EMAIL),("fm",FM_EMAIL),
                     ("os",OS_EMAIL),("mm",MM_EMAIL),("client",CLI_EMAIL)]:
    r = c.post(f"{BASE}/auth/login", json={"email": email, "password": PASS})
    if r.status_code == 200 and r.json().get("success"):
        tokens[label] = r.json()["data"]["access_token"]
        r2 = c.get(f"{BASE}/auth/me", headers=H(label))
        if r2.status_code == 200:
            ids[label] = r2.json()["data"]["id"]
        print(f"  [OK] {label} logged in")
    else:
        print(f"  [FAIL] {label}: {r.text[:100]}")

# Create a fresh operation for milestone/audit tests
client_id = ids.get("client")
lo_id  = ids.get("lo")
fm_id  = ids.get("fm")
os_id  = ids.get("os")
mm_id  = ids.get("mm")

print("\n=== SETUP: Create operation ===")
r = c.post(f"{BASE}/operations", headers=H("bm"), json={
    "type": "full_operation",
    "client_id": client_id,
    "expected_volume_mt": 300.0,
    "currency": "USD",
    "notes": "Phase 5 hardening test"
})
if r.status_code == 201 and r.json().get("success"):
    ids["op"] = r.json()["data"]["id"]
    print(f"  [OK] operation created: {r.json()['data']['operation_number']}")
else:
    print(f"  [FAIL] create operation: {r.text[:200]}")
    sys.exit(1)

op_id = ids["op"]

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 1. Request ID Middleware ===")

r = c.get(f"{BASE}/health")
req_id = r.headers.get("X-Request-ID", "")
check("health response has X-Request-ID header", bool(req_id), f"id={req_id[:8]}...")

r2 = c.get(f"{BASE}/health")
req_id2 = r2.headers.get("X-Request-ID", "")
check("each request gets a unique X-Request-ID", req_id != req_id2,
      f"{req_id[:8]} != {req_id2[:8]}")

# Client-supplied X-Request-ID is echoed back
custom_id = "my-custom-req-id-12345"
r3 = c.get(f"{BASE}/health", headers={"X-Request-ID": custom_id})
echoed = r3.headers.get("X-Request-ID", "")
check("client-supplied X-Request-ID is echoed back", echoed == custom_id,
      f"got={echoed}")

# Auth endpoint also includes X-Request-ID
r4 = c.get(f"{BASE}/auth/me", headers=H("bm"))
check("authenticated endpoint returns X-Request-ID", bool(r4.headers.get("X-Request-ID")))

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 2. Rate Limiting ===")

# Login endpoint: 10/60s limit per IP — we test that repeated calls eventually 429
# We only try 12 times to trigger it (limit is 10)
hit_429 = False
for i in range(12):
    r = c.post(f"{BASE}/auth/login",
               json={"email": "nonexistent@test.invalid", "password": "wrong"})
    if r.status_code == 429:
        hit_429 = True
        retry_after = r.headers.get("Retry-After", "?")
        print(f"         429 hit on attempt {i+1}, Retry-After={retry_after}s")
        break

check("login endpoint returns 429 after repeated attempts", hit_429)

if hit_429:
    check("429 response has Retry-After header",
          bool(r.headers.get("Retry-After")))
    body = r.json()
    check("429 response body has success=false",
          body.get("success") is False)

# Non-rate-limited endpoint is not affected
r_health = c.get(f"{BASE}/health")
check("GET /health is not rate limited (returns 200)", r_health.status_code == 200)

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 3. Client Milestones — Auto-creation on Transitions ===")

# Advance operation through tasks_assigned (milestone: team_assigned)
# Create individual task assignments then transition
for user_id, task_type in [
    (lo_id, "truck_logistics"),
    (fm_id, "finance_processing"),
    (os_id, "vessel_operations"),
    (mm_id, "marine_discharge"),
]:
    c.post(f"{BASE}/operations/{op_id}/tasks", headers=H("bm"), json={
        "assigned_to": user_id, "task_type": task_type, "priority": "normal"
    })
r = c.post(f"{BASE}/operations/{op_id}/transition", headers=H("bm"),
           json={"to_status": "tasks_assigned", "reason": "All tasks assigned"})
print(f"  tasks_assigned transition: {r.status_code}")

# Check milestones — should have team_assigned now
r = c.get(f"{BASE}/portal/operations/{op_id}/milestones", headers=H("client"))
if r.status_code == 200 and r.json().get("success"):
    milestones = r.json()["data"]
    m_types = [m["milestone_type"] for m in milestones]
    print(f"         milestones after tasks_assigned: {m_types}")
    check("team_assigned milestone created after task assignment",
          "team_assigned" in m_types)
else:
    check("portal milestones endpoint returns 200", False,
          f"got {r.status_code}: {r.text[:200]}")

# Advance to awaiting_feedback
r = c.post(f"{BASE}/operations/{op_id}/transition", headers=H("bm"),
           json={"to_status": "awaiting_feedback", "reason": "Requesting truck readiness"})
print(f"  awaiting_feedback: {r.status_code}")

# Get a truck, submit feedback, approve
r_truck = c.get(f"{BASE}/trucks", headers=H("bm"))
truck_items = r_truck.json()["data"] if r_truck.status_code == 200 else []
if isinstance(truck_items, dict):
    truck_items = truck_items.get("items", [])
if truck_items:
    ids["truck"] = truck_items[0]["id"]

if ids.get("truck"):
    r = c.post(f"{BASE}/operations/{op_id}/feedback", headers=H("lo"), json={
        "truck_ids": [ids["truck"]],
        "readiness_summary": "All trucks ready for Phase 5 test",
        "truck_details": {"trucks": [{"id": ids["truck"], "status": "ready"}]},
    })
    print(f"  feedback_submitted: {r.status_code}")
    r = c.post(f"{BASE}/operations/{op_id}/transition", headers=H("bm"),
               json={"to_status": "feedback_approved", "reason": "Feedback looks good"})
    print(f"  feedback_approved: {r.status_code}")

# Check milestones again — should now also have logistics_confirmed
r = c.get(f"{BASE}/portal/operations/{op_id}/milestones", headers=H("client"))
if r.status_code == 200 and r.json().get("success"):
    milestones = r.json()["data"]
    m_types = [m["milestone_type"] for m in milestones]
    print(f"         milestones after feedback_approved: {m_types}")
    check("logistics_confirmed milestone created after feedback approval",
          "logistics_confirmed" in m_types)
    check("milestones are ordered chronologically",
          milestones == sorted(milestones, key=lambda m: m["reached_at"]))
    check("milestone has required fields",
          all("title" in m and "description" in m and "reached_at" in m
              for m in milestones))

# Non-client roles cannot access milestones endpoint
fail("BM cannot access portal milestones endpoint",
     c.get(f"{BASE}/portal/operations/{op_id}/milestones", headers=H("bm")), 403)

# Client cannot access another client's milestones
fail("Client cannot access milestones for unknown operation",
     c.get(f"{BASE}/portal/operations/00000000-0000-0000-0000-000000000000/milestones",
           headers=H("client")), 404)

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 4. Audit Log Endpoint ===")

d = ok("BM can retrieve operation audit log",
       c.get(f"{BASE}/operations/{op_id}/audit-log", headers=H("bm")))
if d:
    logs = d.get("data", [])
    print(f"         audit entries: {len(logs)}")
    check("audit log returns a list", isinstance(logs, list))
    if logs:
        first = logs[0]
        check("audit entry has action field", "action" in first)
        check("audit entry has user_id field", "user_id" in first)
        check("audit entry has created_at field", "created_at" in first)
        # CREATE_OPERATION should be the first action
        actions = [l["action"] for l in logs]
        check("CREATE_OPERATION is logged", "CREATE_OPERATION" in actions)
        check("TRANSITION_OPERATION is logged", "TRANSITION_OPERATION" in actions)

# Non-BM roles are blocked
fail("FM cannot access audit log",
     c.get(f"{BASE}/operations/{op_id}/audit-log", headers=H("fm")), 403)
fail("OS cannot access audit log",
     c.get(f"{BASE}/operations/{op_id}/audit-log", headers=H("os")), 403)
fail("Client cannot access audit log",
     c.get(f"{BASE}/operations/{op_id}/audit-log", headers=H("client")), 403)
fail("Unauthenticated cannot access audit log",
     c.get(f"{BASE}/operations/{op_id}/audit-log"), 401)

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 5. Startup Config Validation (server is running = passed) ===")

# If the server started successfully with our new validation code, config
# validation ran without crashing.
r = c.get(f"{BASE}/health")
check("server started successfully with config validation in place",
      r.status_code == 200)

# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
passed = sum(1 for t,_,_ in results if t == "PASS")
failed = sum(1 for t,_,_ in results if t == "FAIL")
total  = len(results)
print(f"PHASE 5 RESULTS: {passed}/{total} PASSED, {failed} FAILED")
if failed:
    print("\nFailed tests:")
    for tag, name, code in results:
        if tag == "FAIL":
            print(f"  - {name} (got {code})")
print("="*60)
sys.exit(0 if failed == 0 else 1)
