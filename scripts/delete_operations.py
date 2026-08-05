"""Permanently delete operations and everything hanging off them.

DESTRUCTIVE AND IRREVERSIBLE. Run scripts/backup_operations.py first and keep
the output — this script refuses to run without being pointed at a backup
directory containing every operation it is about to destroy.

Dry run (default — changes nothing):
    ./venv/Scripts/python.exe scripts/delete_operations.py \
        --backup ../db_backups/operations-XXXX RA-2026-0048 ...

Execute:
    ... same command ... --confirm DELETE

Deletes children before parents in one transaction, so a failure anywhere
rolls the whole thing back and nothing is half-removed.
"""
import json
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent

# Children first. Mirrors BY_OPERATION in backup_operations.py — keep in step.
DELETE_ORDER = [
    "vessel_discharge_events", "terminal_loading_receipts", "client_milestones",
    "client_notification_logs", "operation_notifications", "notifications",
    "pfi_allocations", "payments", "vouchers", "invoices",
    "truck_safety_audits", "rob_entries",
    "truck_bdns", "bdns",
    "truck_operations", "truck_feedback",
    "task_assignments", "documents", "operation_products",
    "operation_status_history", "audit_logs",
]

# Reached via vessel_activities. These run AFTER the operation-scoped tables
# above, not before: bdns.vessel_leg_id points at vessel_activity_legs, so the
# BDNs have to be gone before a leg can be removed. Within this list,
# vessel_activity_updates.leg_id points at legs, so updates precede legs.
VIA_VESSEL_ACTIVITY = [
    ("vessel_activity_updates", "vessel_activity_id"),
    ("vessel_activity_comments", "vessel_activity_id"),
    ("vessel_activity_legs", "vessel_activity_id"),
]


def _db_url() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("SYNC_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("SYNC_DATABASE_URL not found in .env")


def main(numbers: list[str], backup_dir: Path, execute: bool) -> None:
    bfile = backup_dir / "backup.json"
    if not bfile.exists():
        raise SystemExit(f"ABORT — no backup.json in {backup_dir}")
    backed_up = {o["operation_number"] for o in json.loads(bfile.read_text(encoding="utf-8"))["operations"]}
    unbacked = set(numbers) - backed_up
    if unbacked:
        raise SystemExit(f"ABORT — these are not in the backup: {sorted(unbacked)}")
    print(f"backup verified: {bfile}  ({len(backed_up)} operations)\n")

    eng = create_engine(_db_url())
    with eng.begin() as c:
        rows = list(c.execute(
            text("select id, operation_number from operations where operation_number = any(:n)"),
            {"n": numbers},
        ))
        op_ids = [r.id for r in rows]
        if len(op_ids) != len(numbers):
            found = {r.operation_number for r in rows}
            raise SystemExit(f"ABORT — not found: {sorted(set(numbers) - found)}")

        # Nothing outside the set may depend on these.
        orphans = list(c.execute(
            text("select operation_number from operations "
                 "where parent_operation_id = any(:ids) and id <> all(:ids)"),
            {"ids": op_ids},
        ))
        if orphans:
            raise SystemExit(f"ABORT — revisions outside the set depend on these: "
                             f"{[r.operation_number for r in orphans]}")

        va_ids = [r.id for r in c.execute(
            text("select id from vessel_activities where operation_id = any(:ids)"), {"ids": op_ids}
        )]

        total = 0
        print(f"{'TABLE':<32} ROWS")
        for table in DELETE_ORDER:
            n = c.execute(text(f"select count(*) from {table} where operation_id = any(:ids)"),
                          {"ids": op_ids}).scalar() or 0
            if n:
                print(f"  {table:<30} {n}")
                total += n
            if execute and n:
                c.execute(text(f"delete from {table} where operation_id = any(:ids)"), {"ids": op_ids})

        for table, col in VIA_VESSEL_ACTIVITY:
            if not va_ids:
                continue
            n = c.execute(text(f"select count(*) from {table} where {col} = any(:ids)"),
                          {"ids": va_ids}).scalar() or 0
            if n:
                print(f"  {table:<30} {n}")
                total += n
            if execute and n:
                c.execute(text(f"delete from {table} where {col} = any(:ids)"), {"ids": va_ids})

        if va_ids:
            print(f"  {'vessel_activities':<30} {len(va_ids)}")
            total += len(va_ids)
            if execute:
                c.execute(text("delete from vessel_activities where operation_id = any(:ids)"),
                          {"ids": op_ids})

        print(f"  {'operations':<30} {len(op_ids)}")
        total += len(op_ids)
        if execute:
            # Clear self-references first so revision links can't block us.
            c.execute(text("update operations set parent_operation_id = NULL "
                           "where id = any(:ids) and parent_operation_id = any(:ids)"),
                      {"ids": op_ids})
            c.execute(text("delete from operations where id = any(:ids)"), {"ids": op_ids})

        print(f"\n  TOTAL: {total} rows")
        if not execute:
            print("\nDRY RUN — nothing was changed. Re-run with --confirm DELETE to execute.")
            c.rollback()
        else:
            print("\nDELETED. Transaction committed.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--backup" not in args:
        raise SystemExit(__doc__)
    i = args.index("--backup")
    backup_dir = Path(args[i + 1])
    if not backup_dir.is_absolute():
        backup_dir = (ROOT / backup_dir).resolve()
    execute = False
    if "--confirm" in args:
        j = args.index("--confirm")
        execute = args[j + 1] == "DELETE"
        del args[j:j + 2]
    del args[i:i + 2]
    if not args:
        raise SystemExit("no operation numbers given")
    main(args, backup_dir, execute)
