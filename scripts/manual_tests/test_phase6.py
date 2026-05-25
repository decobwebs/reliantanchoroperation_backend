"""
Phase 6 Test Suite — RAOMS Invoice Generation
Tests: invoice creation, lifecycle (draft→sent→paid), access control,
       client portal invoice visibility, operation completion on payment.
Run: python -X utf8 test_phase6.py
"""
import httpx, sys
from datetime import datetime, timezone, timedelta

BASE = "http://localhost:8007/api/v1"
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

client_id = ids.get("client")
lo_id  = ids.get("lo")
fm_id  = ids.get("fm")
os_id  = ids.get("os")
mm_id  = ids.get("mm")

# ── Full lifecycle setup: operation → bdn_approved ─────────────────────────────
print("\n=== SETUP: Advance operation to bdn_approved ===")

r = c.post(f"{BASE}/operations", headers=H("bm"), json={
    "type": "full_operation", "client_id": client_id,
    "expected_volume_mt": 400.0, "currency": "USD", "notes": "Phase 6 invoice test"
})
if r.status_code not in (200, 201):
    print(f"  [FAIL] create op: {r.text[:200]}"); sys.exit(1)
ids["op"] = r.json()["data"]["id"]
op_id = ids["op"]
print(f"  [OK] operation: {r.json()['data']['operation_number']}")

# Tasks
for uid, tt in [(lo_id,"truck_logistics"),(fm_id,"finance_processing"),
                (os_id,"vessel_operations"),(mm_id,"marine_discharge")]:
    c.post(f"{BASE}/operations/{op_id}/tasks", headers=H("bm"),
           json={"assigned_to": uid, "task_type": tt, "priority": "normal"})
c.post(f"{BASE}/operations/{op_id}/transition", headers=H("bm"),
       json={"to_status": "tasks_assigned", "reason": "assigned"})
c.post(f"{BASE}/operations/{op_id}/transition", headers=H("bm"),
       json={"to_status": "awaiting_feedback", "reason": "requesting"})

# Truck feedback
r_truck = c.get(f"{BASE}/trucks", headers=H("bm"))
t_items = r_truck.json()["data"] if r_truck.status_code == 200 else []
if isinstance(t_items, dict): t_items = t_items.get("items", [])
if t_items:
    ids["truck"] = t_items[0]["id"]
    c.post(f"{BASE}/operations/{op_id}/feedback", headers=H("lo"), json={
        "truck_ids": [ids["truck"]],
        "readiness_summary": "All good",
        "truck_details": {},
    })
c.post(f"{BASE}/operations/{op_id}/transition", headers=H("bm"),
       json={"to_status": "feedback_approved", "reason": "approved"})

# PFI + payment
r_pfi = c.post(f"{BASE}/operations/{op_id}/pfis", headers=H("bm"), json={
    "amount": "40000.00", "currency": "USD", "supplier_name": "Phase6 Fuel"
})
if r_pfi.status_code == 201:
    ids["pfi"] = r_pfi.json()["data"]["id"]
    r_pay = c.post(f"{BASE}/operations/{op_id}/payments", headers=H("fm"), json={
        "pfi_id": ids["pfi"], "amount": "40000.00", "currency": "USD",
        "payment_method": "wire", "payment_reference": "P6-REF",
        "payment_date": datetime.now(timezone.utc).isoformat(),
    })
    if r_pay.status_code == 201:
        ids["payment"] = r_pay.json()["data"]["id"]
        c.post(f"{BASE}/operations/{op_id}/payments/{ids['payment']}/confirm",
               headers=H("fm"), json={"notes": "confirmed"})

# Vessel + BDN
r_vessels = c.get(f"{BASE}/vessels", headers=H("bm"))
v_items = r_vessels.json()["data"] if r_vessels.status_code == 200 else []
if isinstance(v_items, dict): v_items = v_items.get("items", [])
if v_items:
    ids["vessel"] = v_items[0]["id"]

c.post(f"{BASE}/operations/{op_id}/transition", headers=H("os"),
       json={"to_status": "vessel_operations", "reason": "starting"})

if ids.get("vessel"):
    r_bdn = c.post(f"{BASE}/operations/{op_id}/bdns", headers=H("mm"), json={
        "vessel_id": ids["vessel"], "quantity_delivered_mt": "390.00",
        "fuel_type": "VLSFO",
        "delivery_date": datetime.now(timezone.utc).isoformat(),
    })
    if r_bdn.status_code == 201:
        ids["bdn"] = r_bdn.json()["data"]["id"]
        bdn_number = r_bdn.json()["data"]["bdn_number"]
        r_approve = c.post(f"{BASE}/bdns/{ids['bdn']}/approve", headers=H("bm"),
                           json={"notes": "approved"})
        print(f"  bdn_approved: {r_approve.status_code}")
        print(f"  [OK] BDN: {bdn_number}")
    else:
        print(f"  [WARN] BDN create: {r_bdn.status_code} {r_bdn.text[:200]}")
else:
    print("  [WARN] no vessel found — BDN skipped")

if not ids.get("bdn"):
    print("  [FAIL] BDN not created — cannot test invoices"); sys.exit(1)

# Check operation status
r_op = c.get(f"{BASE}/operations/{op_id}", headers=H("bm"))
op_status = r_op.json()["data"]["status"] if r_op.status_code == 200 else "unknown"
print(f"  operation status before invoice: {op_status}")

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 1. Create Invoice ===")

due = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
d = ok("FM can create invoice from approved BDN",
       c.post(f"{BASE}/operations/{op_id}/invoices", headers=H("fm"), json={
           "bdn_id": ids["bdn"],
           "amount": "39000.00",
           "currency": "USD",
           "tax_amount": "1950.00",
           "due_date": due,
           "notes": "Phase 6 test invoice",
       }), 201, show="invoice_number")

if d:
    inv = d.get("data", {})
    ids["invoice"] = inv.get("id")
    ids["invoice_number"] = inv.get("invoice_number")
    print(f"         invoice: {inv.get('invoice_number')}, total: {inv.get('total_amount')}")

    check("invoice status is draft", inv.get("status") == "draft")
    check("invoice total = amount + tax", inv.get("total_amount") == "40950.00")
    check("invoice has invoice_number", bool(inv.get("invoice_number")))
    check("invoice_number starts with INV-", inv.get("invoice_number","").startswith("INV-"))

# Operation should now be invoiced
r_op2 = c.get(f"{BASE}/operations/{op_id}", headers=H("bm"))
if r_op2.status_code == 200:
    new_status = r_op2.json()["data"]["status"]
    check("operation transitions to invoiced after invoice creation",
          new_status == "invoiced", f"got={new_status}")

# ── Duplicate invoice blocked ──────────────────────────────────────────────────
fail("Cannot create duplicate invoice for same BDN",
     c.post(f"{BASE}/operations/{op_id}/invoices", headers=H("fm"), json={
         "bdn_id": ids["bdn"], "amount": "100.00", "currency": "USD"
     }), 409)

# ── Access control ─────────────────────────────────────────────────────────────
fail("BM cannot create invoice",
     c.post(f"{BASE}/operations/{op_id}/invoices", headers=H("bm"), json={
         "bdn_id": ids["bdn"], "amount": "100.00", "currency": "USD"
     }), 403)
fail("OS cannot create invoice",
     c.post(f"{BASE}/operations/{op_id}/invoices", headers=H("os"), json={
         "bdn_id": ids["bdn"], "amount": "100.00", "currency": "USD"
     }), 403)
fail("Client cannot create invoice",
     c.post(f"{BASE}/operations/{op_id}/invoices", headers=H("client"), json={
         "bdn_id": ids["bdn"], "amount": "100.00", "currency": "USD"
     }), 403)

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 2. List & Get Invoice ===")

ok("FM can list operation invoices",
   c.get(f"{BASE}/operations/{op_id}/invoices", headers=H("fm")))
ok("BM can list operation invoices",
   c.get(f"{BASE}/operations/{op_id}/invoices", headers=H("bm")))

if ids.get("invoice"):
    ok("FM can get single invoice",
       c.get(f"{BASE}/invoices/{ids['invoice']}", headers=H("fm")))
    ok("BM can get single invoice",
       c.get(f"{BASE}/invoices/{ids['invoice']}", headers=H("bm")))
    fail("OS cannot get invoice",
         c.get(f"{BASE}/invoices/{ids['invoice']}", headers=H("os")), 403)
    fail("Client cannot get invoice via internal endpoint",
         c.get(f"{BASE}/invoices/{ids['invoice']}", headers=H("client")), 403)

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 3. Invoice Lifecycle: draft → sent ===")

if ids.get("invoice"):
    d = ok("FM can send invoice (mark as sent)",
           c.post(f"{BASE}/invoices/{ids['invoice']}/send", headers=H("fm"), json={
               "pdf_url": "https://storage.example.com/inv/phase6.pdf",
               "notes": "Sent to client",
           }))
    if d:
        inv = d.get("data", {})
        check("invoice status is now sent", inv.get("status") == "sent")
        check("invoice has pdf_url", bool(inv.get("pdf_url")))
        check("invoice has sent_at timestamp", bool(inv.get("sent_at")))

    # Cannot send again
    fail("Cannot send an already-sent invoice",
         c.post(f"{BASE}/invoices/{ids['invoice']}/send", headers=H("fm"),
                json={}), 422)

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 4. Client Portal: Invoice Visibility ===")

# Client sees sent invoices in their portal
d = ok("Client can view invoices via portal",
       c.get(f"{BASE}/portal/operations/{op_id}/invoices", headers=H("client")))
if d:
    portal_invs = d.get("data", [])
    check("portal invoices returns a list", isinstance(portal_invs, list))
    if portal_invs:
        pi = portal_invs[0]
        check("portal invoice has invoice_number", "invoice_number" in pi)
        check("portal invoice has total_amount", "total_amount" in pi)
        check("portal invoice has status", "status" in pi)
        check("portal invoice has due_date", "due_date" in pi)

fail("BM cannot use portal invoice endpoint",
     c.get(f"{BASE}/portal/operations/{op_id}/invoices", headers=H("bm")), 403)
fail("Client cannot view invoices for operation they don't own",
     c.get(f"{BASE}/portal/operations/00000000-0000-0000-0000-000000000000/invoices",
           headers=H("client")), 404)

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 5. Invoice Lifecycle: sent → paid ===")

if ids.get("invoice"):
    d = ok("FM can mark invoice as paid",
           c.post(f"{BASE}/invoices/{ids['invoice']}/mark-paid", headers=H("fm"),
                  json={"notes": "Client paid via wire"}))
    if d:
        inv = d.get("data", {})
        check("invoice status is paid", inv.get("status") == "paid")
        check("invoice has paid_at timestamp", bool(inv.get("paid_at")))

    # Operation should now be completed
    r_op3 = c.get(f"{BASE}/operations/{op_id}", headers=H("bm"))
    if r_op3.status_code == 200:
        final_status = r_op3.json()["data"]["status"]
        check("operation transitions to completed after invoice paid",
              final_status == "completed", f"got={final_status}")

    # Cannot mark paid again
    fail("Cannot mark already-paid invoice as paid again",
         c.post(f"{BASE}/invoices/{ids['invoice']}/mark-paid", headers=H("fm"),
                json={}), 422)

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 6. Invoice Cancellation ===")

# Create a second operation + BDN for cancel test
print("  (creating second operation for cancel test)")
r2 = c.post(f"{BASE}/operations", headers=H("bm"), json={
    "type": "full_operation", "client_id": client_id,
    "expected_volume_mt": 100.0, "currency": "USD", "notes": "Phase 6 cancel test"
})
if r2.status_code in (200, 201):
    op2_id = r2.json()["data"]["id"]
    # Walk it to bdn_approved quickly
    for uid, tt in [(lo_id,"truck_logistics"),(fm_id,"finance_processing"),
                    (os_id,"vessel_operations"),(mm_id,"marine_discharge")]:
        c.post(f"{BASE}/operations/{op2_id}/tasks", headers=H("bm"),
               json={"assigned_to": uid, "task_type": tt, "priority": "normal"})
    c.post(f"{BASE}/operations/{op2_id}/transition", headers=H("bm"),
           json={"to_status": "tasks_assigned", "reason": "x"})
    c.post(f"{BASE}/operations/{op2_id}/transition", headers=H("bm"),
           json={"to_status": "awaiting_feedback", "reason": "x"})
    if ids.get("truck"):
        c.post(f"{BASE}/operations/{op2_id}/feedback", headers=H("lo"), json={
            "truck_ids": [ids["truck"]], "readiness_summary": "ok", "truck_details": {}
        })
    c.post(f"{BASE}/operations/{op2_id}/transition", headers=H("bm"),
           json={"to_status": "feedback_approved", "reason": "x"})
    r_pfi2 = c.post(f"{BASE}/operations/{op2_id}/pfis", headers=H("bm"),
                    json={"amount": "5000.00", "currency": "USD"})
    if r_pfi2.status_code == 201:
        pfi2_id = r_pfi2.json()["data"]["id"]
        r_pay2 = c.post(f"{BASE}/operations/{op2_id}/payments", headers=H("fm"), json={
            "pfi_id": pfi2_id, "amount": "5000.00", "currency": "USD",
            "payment_method": "wire", "payment_reference": "P6C-REF",
            "payment_date": datetime.now(timezone.utc).isoformat(),
        })
        if r_pay2.status_code == 201:
            c.post(f"{BASE}/operations/{op2_id}/payments/{r_pay2.json()['data']['id']}/confirm",
                   headers=H("fm"), json={})
    c.post(f"{BASE}/operations/{op2_id}/transition", headers=H("os"),
           json={"to_status": "vessel_operations", "reason": "x"})
    if ids.get("vessel"):
        r_bdn2 = c.post(f"{BASE}/operations/{op2_id}/bdns", headers=H("os"), json={
            "vessel_id": ids["vessel"], "quantity_mt": "95.00", "fuel_type": "MGO",
            "delivery_date": datetime.now(timezone.utc).isoformat(),
        })
        if r_bdn2.status_code == 201:
            bdn2_id = r_bdn2.json()["data"]["id"]
            c.post(f"{BASE}/bdns/{bdn2_id}/approve", headers=H("bm"), json={})
            # Create a draft invoice to cancel
            r_inv2 = c.post(f"{BASE}/operations/{op2_id}/invoices", headers=H("fm"), json={
                "bdn_id": bdn2_id, "amount": "4800.00", "currency": "USD"
            })
            if r_inv2.status_code == 201:
                inv2_id = r_inv2.json()["data"]["id"]
                d = ok("FM can cancel a draft invoice",
                       c.post(f"{BASE}/invoices/{inv2_id}/cancel", headers=H("fm")))
                if d:
                    check("cancelled invoice status is cancelled",
                          d.get("data", {}).get("status") == "cancelled")
                fail("Cannot cancel an already-cancelled invoice",
                     c.post(f"{BASE}/invoices/{inv2_id}/cancel", headers=H("fm")), 422)

# ══════════════════════════════════════════════════════════════════════════════
print("\n=== 7. Invoice Number Sequencing ===")

# Already generated at least one invoice — verify format
if ids.get("invoice_number"):
    inv_num = ids["invoice_number"]
    parts = inv_num.split("-")
    check("invoice number has correct format INV-YYYY-NNNN", len(parts) == 3 and parts[0] == "INV",
          f"got={inv_num}")
    check("invoice year is correct", parts[1] == "2026", f"got={parts[1]}")
    check("invoice sequence is 4 digits", len(parts[2]) == 4, f"got={parts[2]}")

# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "="*60)
passed = sum(1 for t,_,_ in results if t == "PASS")
failed = sum(1 for t,_,_ in results if t == "FAIL")
total  = len(results)
print(f"PHASE 6 RESULTS: {passed}/{total} PASSED, {failed} FAILED")
if failed:
    print("\nFailed tests:")
    for tag, name, code in results:
        if tag == "FAIL":
            print(f"  - {name} (got {code})")
print("="*60)
sys.exit(0 if failed == 0 else 1)
