"""Merge trucks that were registered twice under slightly different plates.

"DMS 117 XA" and "DMS117XA", or "NCH897XA" and "NCH897XA." — the same truck,
entered a second time because the first could not be found. Each pair splits
one truck's history across two records. On 29 Oct 2026 there were 10 pairs.

DRY RUN BY DEFAULT. Without --apply it only reads and prints the plan.

    ./venv/Scripts/python.exe scripts/merge_duplicate_trucks.py            # show the plan
    ./venv/Scripts/python.exe scripts/merge_duplicate_trucks.py --apply --by bm@example.com

--by names the user the merge is recorded against in the audit log (the
person who approved it).

For each group the record used on the most jobs is kept (oldest on a tie).
Every reference to the others — found from the database's own foreign keys,
so a table added later is not missed — is moved onto it, along with any truck
ids inside readiness reports. The others are retired (is_active = false) with
a note naming the record they were merged into; nothing is deleted. Licence,
calibration certificate, photo and chassis carry across when the kept record
has none.

--apply writes a JSON backup of every row it is about to touch to
db_backups/ first, then does all groups in ONE transaction: any error and
nothing changes.
"""
import json
import re
import sys
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent

# Must match app.services.truck_availability.plate_key.
KEY_SQL = "upper(regexp_replace(truck_number, '[^A-Za-z0-9]', '', 'g'))"
_INVISIBLE = re.compile(r"[​-‏⁠﻿]")
CARRY = ("truck_licence_url", "calibration_cert_url", "photo_url", "chassis_number", "ownership")


def _db_url() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("SYNC_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("SYNC_DATABASE_URL not found in .env")


def _encode(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, (Decimal, UUID)):
        return str(o)
    raise TypeError(f"unserialisable: {type(o)}")


def _references(conn):
    """Every (table, column) with a foreign key onto trucks.id."""
    return [tuple(r) for r in conn.execute(text("""
        SELECT kcu.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
          AND ccu.table_name = 'trucks' AND ccu.column_name = 'id'
        ORDER BY 1, 2
    """))]


def _groups(conn):
    keys = [r[0] for r in conn.execute(text(
        f"SELECT {KEY_SQL} FROM trucks GROUP BY 1 HAVING count(*) > 1 ORDER BY 1"))]
    out = []
    for key in keys:
        rows = conn.execute(text(f"""
            SELECT t.*, (SELECT count(*) FROM truck_operations o WHERE o.truck_id = t.id) AS jobs
            FROM trucks t WHERE {KEY_SQL} = :k
            ORDER BY jobs DESC, t.created_at ASC
        """), {"k": key}).mappings().all()
        out.append([dict(r) for r in rows])
    return out


def main(apply: bool, by_email: str | None) -> None:
    engine = create_engine(_db_url())
    with engine.begin() as conn:
        if not apply:
            conn.execute(text("SET TRANSACTION READ ONLY"))
        by_id = None
        if apply:
            if not by_email:
                raise SystemExit("--apply needs --by <email of the approving user>")
            by_id = conn.execute(text("SELECT id FROM users WHERE lower(email) = lower(:e)"), {"e": by_email}).scalar()
            if not by_id:
                raise SystemExit(f"No user with email {by_email}")
        refs = _references(conn)
        groups = _groups(conn)
        if not groups:
            print("No duplicate plates. Nothing to do.")
            return

        print(f"Tables pointing at trucks: {', '.join(f'{t}.{c}' for t, c in refs)}")
        print(f"\n{len(groups)} duplicate group(s):\n")
        backup = {"taken_at": datetime.utcnow().isoformat(), "groups": []}

        for group in groups:
            keep, dups = group[0], group[1:]
            print(f"  KEEP   {keep['truck_number']!r:<16} {keep['jobs']} job(s)  active={keep['is_active']}")
            entry = {"keep": keep, "merged": [], "moved": {}}
            for d in dups:
                counts = {f"{t}.{c}": conn.execute(text(f"SELECT count(*) FROM {t} WHERE {c} = :i"), {"i": d["id"]}).scalar()
                          for t, c in refs}
                fb = conn.execute(text("SELECT id, truck_ids FROM truck_feedback WHERE truck_ids::text LIKE :p"),
                                  {"p": f'%{d["id"]}%'}).mappings().all()
                moving = ", ".join(f"{k}={v}" for k, v in counts.items() if v) or "nothing"
                print(f"  MERGE  {d['truck_number']!r:<16} {d['jobs']} job(s)  -> moves {moving}; readiness reports={len(fb)}")
                entry["merged"].append(d)
                entry["moved"][str(d["id"])] = {
                    "rows": {f"{t}.{c}": [str(r[0]) for r in conn.execute(text(f"SELECT id FROM {t} WHERE {c} = :i"), {"i": d["id"]})]
                             for t, c in refs},
                    "truck_feedback": [{"id": str(r["id"]), "truck_ids": r["truck_ids"]} for r in fb],
                }
            backup["groups"].append(entry)
            print()

        if not apply:
            print("Dry run — nothing changed. Re-run with --apply to merge.")
            return

        out_dir = ROOT.parent / "db_backups"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"truck-merge-{datetime.now():%Y%m%d-%H%M%S}.json"
        path.write_text(json.dumps(backup, default=_encode, indent=2))
        print(f"Backup written: {path}")

        for entry in backup["groups"]:
            keep = entry["keep"]
            for d in entry["merged"]:
                for t, c in refs:
                    conn.execute(text(f"UPDATE {t} SET {c} = :k WHERE {c} = :d"), {"k": keep["id"], "d": d["id"]})
                conn.execute(text(
                    "UPDATE truck_feedback SET truck_ids = replace(truck_ids::text, :d, :k)::jsonb "
                    "WHERE truck_ids::text LIKE :p"),
                    {"d": str(d["id"]), "k": str(keep["id"]), "p": f'%{d["id"]}%'})
                for col in CARRY:
                    if not keep.get(col) and d.get(col):
                        conn.execute(text(f"UPDATE trucks SET {col} = :v WHERE id = :k"), {"v": d[col], "k": keep["id"]})
                        keep[col] = d[col]
                # The retired record's plate gets " (merged)" so it can never
                # collide with the kept one once that is tidied below
                # ("ABC979ZU." -> "ABC979ZU" while a retired "ABC979ZU" exists).
                # The original plate is kept in the note and the audit log.
                note = f"Merged into {keep['truck_number']} on {date.today():%d %b %Y} (duplicate plate, was {d['truck_number']})."
                conn.execute(text(
                    "UPDATE trucks SET is_active = false, updated_at = now(), "
                    "truck_number = left(truck_number, 41) || ' (merged)', "
                    "notes = trim(both from coalesce(notes, '') || ' ' || :n) WHERE id = :d"),
                    {"n": note, "d": d["id"]})
                conn.execute(text(
                    "INSERT INTO audit_logs (id, user_id, action, entity_type, entity_id, changes, reason, created_at) "
                    "VALUES (gen_random_uuid(), :u, 'MERGE_TRUCK', 'truck', :d, CAST(:ch AS jsonb), :r, now())"),
                    {"u": by_id, "d": d["id"], "ch": json.dumps({"merged_into": str(keep["id"]), "plate": d["truck_number"]}),
                     "r": "Duplicate plate merged"})
            cleaned = _INVISIBLE.sub("", keep["truck_number"]).strip().rstrip(".,;:-_").strip()
            if cleaned != keep["truck_number"]:
                conn.execute(text("UPDATE trucks SET truck_number = :n WHERE id = :k"), {"n": cleaned, "k": keep["id"]})
        print(f"Merged {sum(len(g['merged']) for g in backup['groups'])} duplicate record(s).")


if __name__ == "__main__":
    args = sys.argv[1:]
    by = args[args.index("--by") + 1] if "--by" in args and args.index("--by") + 1 < len(args) else None
    main(apply="--apply" in args, by_email=by)
