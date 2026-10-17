"""Permanently delete operations and everything hanging off them.

DESTRUCTIVE AND IRREVERSIBLE. Run scripts/backup_operations.py first and keep
the output — this script refuses to run without being pointed at a backup
directory containing every operation it is about to destroy.

Dry run (default — changes nothing):
    ./venv/Scripts/python.exe scripts/delete_operations.py \
        --backup ../db_backups/operations-XXXX RA-2026-0048 ...

Execute:
    ... same command ... --confirm DELETE

Undo the operations' effect on vessel ROB as well (optional):
    ... --reverse-rob --actor someone@reliantanchor.com

    Takes the net of the operations' rob_entries off each vessel's
    current_rob_mt, in the same transaction as the delete — the same
    delete-and-subtract the app itself does when a BDN is deleted
    (vessel_bdn_service._reverse_rob_entry). The adjustment is applied to the
    live value (current - net), never overwritten with a figure read earlier,
    so ROB changes staff make meanwhile are kept. Each vessel adjusted gets an
    audit_logs row under --actor, since the operation's own audit rows go too.

Deletes children before parents in one transaction, so a failure anywhere
rolls the whole thing back and nothing is half-removed.
"""
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent

# Children first. Mirrors BY_OPERATION in backup_operations.py — keep in step.
DELETE_ORDER = [
    "vessel_discharge_events", "terminal_loading_receipts", "sts_collections", "client_milestones",
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


def _reverse_rob(c, op_ids, numbers, actor_email: str, execute: bool) -> None:
    """Take the operations' net ROB effect off each vessel. Runs inside the
    caller's transaction, before rob_entries are deleted."""
    actor = c.execute(text("select id from users where email = :e and is_active"), {"e": actor_email}).scalar()
    if not actor:
        raise SystemExit(f"ABORT — no active user with email {actor_email}")
    nets = list(c.execute(
        text("select vessel_id, sum(quantity_mt) as net from rob_entries "
             "where operation_id = any(:ids) group by vessel_id"),
        {"ids": op_ids},
    ))
    if not nets:
        print("ROB: these operations never changed a vessel's ROB - nothing to reverse\n")
        return
    print("ROB REVERSAL")
    for r in nets:
        # FOR UPDATE: staff may be recording on this vessel right now; hold the
        # row until commit so nothing lands between the read and the write.
        v = c.execute(text("select vessel_name, current_rob_mt from vessels where id = :v for update"),
                      {"v": r.vessel_id}).one()
        after = v.current_rob_mt - r.net
        print(f"  {v.vessel_name}: {v.current_rob_mt} - ({r.net}) = {after}")
        if execute:
            c.execute(text("update vessels set current_rob_mt = current_rob_mt - :net, updated_at = now() "
                           "where id = :v"), {"net": r.net, "v": r.vessel_id})
            c.execute(text(
                "insert into audit_logs (id, user_id, operation_id, action, entity_type, entity_id, changes, reason, created_at) "
                "values (:id, :u, NULL, 'REVERSE_ROB_FOR_DELETED_OPERATION', 'vessel', :v, cast(:ch as jsonb), :reason, now())"
            ), {
                "id": str(uuid.uuid4()), "u": actor, "v": r.vessel_id,
                "ch": json.dumps({
                    "operations": numbers,
                    "current_rob_mt": {"from": str(v.current_rob_mt), "to": str(after)},
                    "reversed_net_mt": str(r.net),
                }),
                "reason": f"Operation(s) {', '.join(numbers)} deleted on management instruction; "
                          f"their ROB effect is undone. Direct correction by system admin",
            })
    print()


def main(numbers: list[str], backup_dir: Path, execute: bool,
         reverse_rob: bool = False, actor_email: str = "") -> None:
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

        if reverse_rob:
            _reverse_rob(c, op_ids, numbers, actor_email, execute)

        va_ids = [r.id for r in c.execute(
            text("select id from vessel_activities where operation_id = any(:ids)"), {"ids": op_ids}
        )]

        total = 0
        print(f"{'TABLE':<32} ROWS")
        for table in DELETE_ORDER:
            # A table added by a migration not yet run here has nothing to delete.
            if c.execute(text("select to_regclass(:t)"), {"t": table}).scalar() is None:
                continue
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
    reverse_rob = "--reverse-rob" in args
    if reverse_rob:
        args.remove("--reverse-rob")
    actor_email = ""
    if "--actor" in args:
        k = args.index("--actor")
        actor_email = args[k + 1]
        del args[k:k + 2]
    if reverse_rob and not actor_email:
        raise SystemExit("--reverse-rob needs --actor <email> to sign the audit record")
    if not args:
        raise SystemExit("no operation numbers given")
    main(args, backup_dir, execute, reverse_rob, actor_email)
