"""Dump everything belonging to a set of operations, before deleting them.

Read-only. Writes one JSON file per run containing every row from every table
that references the named operations, directly or transitively, plus a
restore-ordered SQL file of INSERT statements.

Usage:
    ./venv/Scripts/python.exe scripts/backup_operations.py RA-2026-0048 RA-2026-0046 ...
    ./venv/Scripts/python.exe scripts/backup_operations.py --file targets.txt

The JSON is the authoritative record; the .sql is a convenience for putting
rows back. Neither is applied automatically — restoring is a deliberate act.
"""
import json
import sys
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent


def _db_url() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("SYNC_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("SYNC_DATABASE_URL not found in .env")


def _encode(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, UUID):
        return str(o)
    if isinstance(o, (bytes, memoryview)):
        return bytes(o).hex()
    raise TypeError(f"unserialisable: {type(o)}")


# Tables holding an operation_id, in reverse dependency order — children before
# parents, so the delete that follows a backup can walk this list top to bottom.
BY_OPERATION = [
    "vessel_discharge_events", "terminal_loading_receipts", "client_milestones",
    "client_notification_logs", "operation_notifications", "notifications",
    "pfi_allocations", "payments", "vouchers", "invoices",
    "truck_safety_audits", "rob_entries",
    "truck_bdns", "bdns",
    "truck_operations", "truck_feedback",
    "task_assignments", "documents", "operation_products",
    "operation_status_history", "vessel_activities", "pfis", "audit_logs",
]

# Children reached through an intermediate table.
BY_PARENT = [
    ("vessel_activity_updates", "vessel_activity_id", "vessel_activities"),
    ("vessel_activity_comments", "vessel_activity_id", "vessel_activities"),
    ("vessel_activity_legs", "vessel_activity_id", "vessel_activities"),
]


def main(numbers: list[str]) -> None:
    eng = create_engine(_db_url())
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT.parent / "db_backups" / f"operations-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, list] = {}
    counts: dict[str, int] = {}

    with eng.connect() as c:
        rows = list(c.execute(
            text("select id, operation_number from operations where operation_number = any(:n)"),
            {"n": numbers},
        ))
        op_ids = [r.id for r in rows]
        found = {r.operation_number for r in rows}
        missing = set(numbers) - found
        if missing:
            raise SystemExit(f"ABORT — not found in database: {sorted(missing)}")

        print(f"backing up {len(op_ids)} operation(s): {', '.join(sorted(found))}\n")

        def dump(table: str, sql: str, params: dict) -> list:
            res = c.execute(text(sql), params)
            data = [dict(m) for m in (r._mapping for r in res)]
            if data:
                payload[table] = payload.get(table, []) + data
                counts[table] = counts.get(table, 0) + len(data)
            return data

        dump("operations", "select * from operations where id = any(:ids)", {"ids": op_ids})

        for t in BY_OPERATION:
            try:
                dump(t, f"select * from {t} where operation_id = any(:ids)", {"ids": op_ids})
            except Exception as exc:  # table may not exist on older schemas
                print(f"  ! skipped {t}: {str(exc).splitlines()[0][:80]}")
                c.rollback()

        va_ids = [r["id"] for r in payload.get("vessel_activities", [])]
        if va_ids:
            for t, col, _ in BY_PARENT:
                try:
                    dump(t, f"select * from {t} where {col} = any(:ids)", {"ids": va_ids})
                except Exception as exc:
                    print(f"  ! skipped {t}: {str(exc).splitlines()[0][:80]}")
                    c.rollback()

        # Safety checks the operator must see before destroying anything.
        warnings = []
        child = list(c.execute(
            text("select operation_number from operations "
                 "where parent_operation_id = any(:ids) and id <> all(:ids)"),
            {"ids": op_ids},
        ))
        if child:
            warnings.append(f"revisions OUTSIDE the target set point at these operations: "
                            f"{[r.operation_number for r in child]}")

        pfi_ids = [r["id"] for r in payload.get("pfis", [])]
        if pfi_ids:
            shared = list(c.execute(
                text("select operation_number from operations "
                     "where pfi_id = any(:p) and id <> all(:ids)"),
                {"p": pfi_ids, "ids": op_ids},
            ))
            if shared:
                warnings.append(f"PFIs are also referenced by operations outside the set: "
                                f"{[r.operation_number for r in shared]}")

    json_path = out_dir / "backup.json"
    json_path.write_text(json.dumps(payload, indent=2, default=_encode), encoding="utf-8")

    # Restore script — parents first, so reverse the delete order.
    lines = ["-- Restore for: " + ", ".join(sorted(found)),
             "-- Generated " + stamp, "BEGIN;"]
    order = ["operations"] + list(reversed(BY_OPERATION)) + [t for t, _, _ in BY_PARENT]
    for table in order:
        for row in payload.get(table, []):
            cols = ", ".join(f'"{k}"' for k in row)
            vals = []
            for v in row.values():
                if v is None:
                    vals.append("NULL")
                elif isinstance(v, bool):
                    vals.append("TRUE" if v else "FALSE")
                elif isinstance(v, (int, float, Decimal)):
                    vals.append(str(v))
                elif isinstance(v, (dict, list)):
                    vals.append("'" + json.dumps(v, default=_encode).replace("'", "''") + "'::jsonb")
                else:
                    vals.append("'" + str(_encode(v) if not isinstance(v, str) else v).replace("'", "''") + "'")
            lines.append(f'INSERT INTO {table} ({cols}) VALUES ({", ".join(vals)});')
    lines.append("COMMIT;")
    (out_dir / "restore.sql").write_text("\n".join(lines), encoding="utf-8")

    total = sum(counts.values())
    print(f"{'TABLE':<32} ROWS")
    for t, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {t:<30} {n}")
    print(f"\n  TOTAL ROWS BACKED UP: {total}")
    print(f"\nwritten to: {out_dir}")
    print(f"  backup.json  {json_path.stat().st_size:,} bytes")
    print(f"  restore.sql  {(out_dir / 'restore.sql').stat().st_size:,} bytes")

    if warnings:
        print("\n*** WARNINGS — read before deleting ***")
        for w in warnings:
            print("  -", w)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    if args[0] == "--file":
        args = [l.strip() for l in Path(args[1]).read_text().splitlines() if l.strip()]
    main(args)
