"""The Command Center — everything the Bunker Manager needs on one screen.

Design rules, in priority order:

1. **Exception first.** The BM should never hunt through tabs to find what is
   wrong. The first thing this returns is the list of things waiting on him,
   oldest and most severe first.
2. **One request, few queries.** The API is in Oregon, the database in
   Frankfurt — roughly 1.2 seconds per round trip — and the connection pool
   is deliberately tiny (see app/database.py; exceeding it has caused an
   outage). So this uses a handful of set-based queries and never runs a
   query inside a loop, never fans out across parallel sessions, and never
   pulls rows it only intends to count.
3. **Degrade, never lie.** A panel that cannot be computed says so, in
   `degraded`. An empty card that silently means "query failed" would read as
   good news, which is the worst possible failure mode for a dashboard whose
   whole job is surfacing problems.

Phase 3 owns this file. It imports the shared toolkit from Phase 1 and does
not modify it. See KPI-WORKLOG.md §7.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.kpi.catalog import CATALOG, rating_for
from app.kpi.schemas_command import (
    AttentionItem, CommandCenterOut, FigureDiscrepancy, FleetLeagueRow, LicenceRunway,
    LiveOperation, LossWatch, MoneyStrip, ProductVolume, RolePulse,
    TeamPulse, VendorLeagueRow, VolumePulse, WorstOperation,
)
from app.kpi.service import _aware, _f, resolve_overrides
from app.models.enums import UserRole, role_label

logger = logging.getLogger("raoms")

# How long an active operation may go without a recorded event before it is
# called stalled. The Operations Manager document requires daily reporting,
# so 24 hours is the document's own implied threshold.
STALL_HOURS = 24

# Licence runway thresholds, in days of remaining cover at the current burn
# rate. Proposals — no document states them; see the blueprint's open items.
RUNWAY_CRITICAL_DAYS = 14
RUNWAY_WARNING_DAYS = 30

_ATTENTION_CAP = 40


def _hours_since(when: Optional[datetime], now: datetime) -> Optional[float]:
    w = _aware(when)
    if w is None:
        return None
    delta = (now - w).total_seconds() / 3600
    return delta if delta >= 0 else 0.0


def _month_bounds(now: datetime):
    this_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_end = this_start - timedelta(seconds=1)
    last_start = last_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return this_start, last_start, last_end


# ── running queries safely without paying for savepoints ──────────────────
#
# The hazard is real: in PostgreSQL one failed statement aborts the whole
# surrounding transaction, and every later query then fails with "current
# transaction is aborted" however well-formed it is. Catching the first
# exception is not enough. Found the hard way — `kpi_targets` not being
# migrated yet made the target lookup fail, and that single failure took down
# all seven panels at once.
#
# The obvious fix, a SAVEPOINT per query, was measured against production and
# cost 685 ms on top of a 248 ms query — nearly three times the query itself,
# about 5.5 s across the dashboard. Far too expensive for the protection.
#
# So instead:
#   * the two optional tables are probed once, cheaply, with to_regclass, so
#     a query is never fired at a table that is not there — which is the only
#     failure that was ever actually expected;
#   * every other query runs bare, and if one does fail the context is marked
#     poisoned. Remaining panels then skip their query entirely and report
#     themselves degraded, instead of issuing statements already certain to
#     fail.
#
# Same guarantee as savepoints — one broken panel never blanks the screen —
# without the per-query cost.


class _Ctx(list):
    """Collects the names of degraded panels.

    Subclasses `list` so panels can keep calling `.append(...)`, while also
    carrying whether the transaction is still usable.
    """
    poisoned = False


class _SkippedQuery(Exception):
    """Raised instead of running a query the transaction can no longer serve."""


async def _fetch_all(db: AsyncSession, ctx: _Ctx, sql, params: Optional[Dict] = None):
    if ctx.poisoned:
        raise _SkippedQuery()
    result = await db.execute(sql, params or {})
    return result.mappings().all()


async def _fetch_one(db: AsyncSession, ctx: _Ctx, sql, params: Optional[Dict] = None):
    if ctx.poisoned:
        raise _SkippedQuery()
    result = await db.execute(sql, params or {})
    return result.mappings().first()


def _note_failure(ctx: _Ctx, panel: str, exc: Exception) -> None:
    ctx.append(panel)
    if isinstance(exc, _SkippedQuery):
        logger.info("Command Center: %s skipped, transaction already failed", panel)
    else:
        logger.error("Command Center: %s failed (%s)", panel, exc)
        ctx.poisoned = True


def _normalise_vendor(name: str) -> str:
    """Strip the differences that are spelling rather than identity.

    Spacing is dropped entirely, not just collapsed: the live data holds both
    "ALT GREEN" and "ALTGREEN", and an earlier version that only collapsed
    runs of whitespace left those as two separate firms in the league table.
    Punctuation goes the same way, which is what folds "3 BROTHER'S" in with
    "3 BROTHER".

    Trailing plurals are then stripped, so "3 BROTHERS" matches too. The three
    rules together reduce seven spellings in production to the three firms
    that actually exist.
    """
    out = "".join(ch for ch in name.upper() if ch.isalnum())
    return out[:-1] if len(out) > 3 and out.endswith("S") else out


def _near_duplicate_vendors(names: List[str]) -> List[tuple]:
    """Pairs of vendor names that normalise to the same thing."""
    seen: Dict[str, str] = {}
    pairs: List[tuple] = []
    for name in names:
        key = _normalise_vendor(name)
        if key in seen and seen[key] != name:
            pairs.append((seen[key], name))
        else:
            seen.setdefault(key, name)
    return pairs


class CommandCenterService:

    @staticmethod
    async def build(db: AsyncSession) -> CommandCenterOut:
        now = datetime.now(timezone.utc)
        this_start, last_start, last_end = _month_bounds(now)
        degraded = _Ctx()

        # Probe the two optional tables once, before anything else. Both now
        # exist (migrations 063 and 064), but this deliberately does not
        # assume so: the module has to keep working against a database that
        # has not been migrated yet, and asking for a table that is not there
        # is the one failure that would otherwise poison the transaction.
        # to_regclass answers without raising.
        has_targets = has_role_scores = False
        try:
            probe = await _fetch_one(db, degraded, text(
                "SELECT to_regclass('public.kpi_targets') AS targets,"
                "       to_regclass('public.kpi_role_scores_current') AS role_scores"
            ))
            has_targets = probe["targets"] is not None
            has_role_scores = probe["role_scores"] is not None
        except Exception as exc:
            logger.info("Command Center: optional-table probe failed (%s)", exc)

        # The live truck loss cap, honouring any BM override. Read through the
        # shared catalog rather than hardcoding 1,500 — the whole point of
        # kpi_targets is that this number moves.
        cap = CATALOG["truck.loss_litres_per_truck"].target
        if has_targets:
            try:
                overrides = await resolve_overrides(db)
                ov = overrides.get("truck.loss_litres_per_truck")
                if ov and ov.target is not None:
                    cap = ov.target
            except Exception as exc:
                _note_failure(degraded, "targets", exc)

        # PANEL ORDER IS LOAD-BEARING. Without savepoints, a failed query
        # poisons the transaction, so panels that run after a failure cannot
        # run at all and report themselves degraded. Everything computed
        # before it survives. So the panels are ordered by how much the BM
        # would miss them: the exception feed and live operations first, the
        # supporting numbers after. Do not reorder for tidiness.
        #
        # Live operations also come before the attention feed because the
        # stalled-operation alerts are derived from its last-event
        # calculation rather than costing a query of their own.
        live = await CommandCenterService._live_operations(db, now, degraded)
        attention = await CommandCenterService._attention(db, now, cap, degraded)
        attention.extend(CommandCenterService._stalled(live))

        order = {"critical": 0, "warning": 1, "info": 2}
        attention.sort(key=lambda i: (order.get(i.severity, 3), -(i.age_hours or 0)))

        volume = await CommandCenterService._volume(db, this_start, last_start, last_end, degraded)
        price, usable, _ = await CommandCenterService._price_per_litre(db, degraded)
        price_basis = (
            f"Median of {usable} PFIs whose implied rate could be a fuel price"
            if price else None
        )
        loss = await CommandCenterService._loss(db, this_start, cap, degraded,
                                                price, price_basis)
        fleet = await CommandCenterService._fleet(db, cap, degraded)
        vendors = await CommandCenterService._vendors(db, cap, degraded)
        discrepancies = await CommandCenterService._discrepancies(db, degraded, price)

        # A large figure gap belongs in the feed, not buried in a panel. This
        # runs here rather than beside the other alerts because it needs the
        # discrepancy pass above; the feed is re-sorted afterwards so these
        # still land in severity order.
        _gap_fail = CATALOG["truck.figure_gap_pct"].fail
        for disc in discrepancies:
            if (disc.gap_pct or 0) < _gap_fail:
                continue
            money = f" (~₦{disc.naira_gap:,.0f})" if disc.naira_gap else ""
            attention.append(AttentionItem(
                kind="figure_gap",
                severity="critical",
                title=f"{disc.bdn_number}: {disc.field} figure differs from the system "
                      f"by {disc.litres_gap:,.0f} L{money}",
                subtitle=f"submitted {disc.submitted:,.0f} vs recorded "
                         f"{disc.system_recorded:,.0f}"
                         + (f" · {disc.submitted_by}" if disc.submitted_by else ""),
                operation_id=disc.operation_id,
                operation_number=disc.operation_number,
            ))
        attention.sort(key=lambda i: (order.get(i.severity, 3), -(i.age_hours or 0)))
        licences = await CommandCenterService._licences(db, degraded)
        money = await CommandCenterService._money(db, this_start, degraded)
        team = await CommandCenterService._team(db, degraded, has_role_scores)

        warnings: List[str] = []
        if volume.vessel_figures_suspect:
            warnings.append(
                "Vessel delivered volume is shown as recorded, not as MT(vac): the BDN "
                "mt_vacuum figures are litres-scale (hundreds of thousands) against a "
                "barge capacity in the low thousands of tonnes. Worth checking how that "
                "field is being filled."
            )
        if loss.naira_lost_estimate:
            warnings.append(
                f"The ₦{loss.naira_lost_estimate:,.0f} figure is an estimate: "
                f"{loss.litres_lost_this_month:,.0f} L valued at ₦{loss.price_per_litre:,.2f}, "
                f"the median rate implied by PFIs. It is not an invoiced amount."
            )
        if loss.trucks_measured and loss.trucks_over_cap / loss.trucks_measured > 0.5:
            warnings.append(
                f"{loss.trucks_over_cap} of {loss.trucks_measured} trucks measured this "
                f"month exceeded the {loss.loss_cap_litres:,.0f} L cap."
            )

        # Vendor names are free text, so the same firm can appear under two
        # spellings and neither row then shows its real record. Worth saying
        # out loud: a league table that silently splits a vendor in two is
        # exactly the kind of quiet wrongness this dashboard exists to catch.
        dupes = _near_duplicate_vendors([v.vendor_name for v in vendors])
        for a, b in dupes:
            warnings.append(
                f'"{a}" and "{b}" look like the same vendor recorded two ways, so '
                f"neither line below shows their full record. Worth merging the "
                f"spelling on the truck records."
            )

        return CommandCenterOut(
            generated_at=now,
            attention=attention[:_ATTENTION_CAP],
            attention_total=len(attention),
            live_operations=live,
            volume=volume,
            loss=loss,
            team=team,
            fleet=fleet,
            vendors=vendors,
            discrepancies=discrepancies,
            licences=licences,
            money=money,
            degraded=degraded,
            data_warnings=warnings,
        )


    # ── the Data Trust Score, per document ────────────────────────────────
    @staticmethod
    async def _discrepancies(db: AsyncSession, ctx: _Ctx,
                             price: Optional[float]) -> List[FigureDiscrepancy]:
        """Truck BDNs whose submitted quantities disagree with the system's.

        RAOMS already snapshots what it had recorded into the system_* columns
        at submission time, precisely so this comparison is possible. Nothing
        had ever read them back. A gap here is not necessarily dishonesty —
        a re-measure, a late correction upstream, a genuine miscount all look
        the same — but it is always worth a human asking why.
        """
        sql = text("""
            SELECT b.truck_bdn_number, o.id AS operation_id, o.operation_number,
                   u.full_name AS submitted_by, d.field, d.submitted, d.system_recorded,
                   abs(d.submitted - d.system_recorded) AS litres_gap,
                   abs(d.submitted - d.system_recorded)
                     / nullif(d.system_recorded, 0) * 100.0 AS gap_pct
            FROM truck_bdns b
            JOIN operations o ON o.id = b.operation_id
            LEFT JOIN users u ON u.id = b.generated_by
            CROSS JOIN LATERAL (VALUES
                ('discharged', b.quantity_discharged_mt, b.system_quantity_discharged_mt),
                ('loaded',     b.quantity_loaded_mt,     b.system_quantity_loaded_mt)
            ) AS d(field, submitted, system_recorded)
            WHERE o.deleted_at IS NULL
              AND d.submitted IS NOT NULL
              AND d.system_recorded IS NOT NULL
              AND d.system_recorded <> 0
              AND abs(d.submitted - d.system_recorded)
                  / nullif(d.system_recorded, 0) * 100.0 > :threshold
            ORDER BY gap_pct DESC
            LIMIT 15
        """)
        try:
            threshold = CATALOG["truck.figure_gap_pct"].target
            rows = await _fetch_all(db, ctx, sql, {"threshold": threshold})
        except Exception as exc:
            _note_failure(ctx, "discrepancies", exc)
            return []

        out: List[FigureDiscrepancy] = []
        for r in rows:
            gap = _f(r["litres_gap"])
            out.append(FigureDiscrepancy(
                bdn_number=r["truck_bdn_number"],
                operation_id=r["operation_id"],
                operation_number=r["operation_number"],
                submitted_by=r["submitted_by"],
                field=r["field"],
                submitted=_f(r["submitted"]),
                system_recorded=_f(r["system_recorded"]),
                litres_gap=gap,
                gap_pct=_f(r["gap_pct"]),
                naira_gap=(gap * price) if (gap and price) else None,
            ))
        return out

    # ── the exception feed ────────────────────────────────────────────────
    @staticmethod
    async def _attention(db: AsyncSession, now: datetime, cap: float,
                         degraded: List[str]) -> List[AttentionItem]:
        """Everything waiting on the BM, gathered in one round trip.

        A UNION ALL rather than six separate queries: these are six different
        tables answering one question, and at 1.2s per round trip the
        difference is a dashboard that opens in a second versus one that
        takes eight.
        """
        sql = text("""
            -- Vessel BDNs awaiting approval
            SELECT 'bdn_pending' AS kind, b.created_at AS since,
                   b.bdn_number AS ref, o.id AS operation_id,
                   o.operation_number AS operation_number, NULL::text AS extra
            FROM bdns b JOIN operations o ON o.id = b.operation_id
            WHERE b.status = 'pending' AND o.deleted_at IS NULL

            UNION ALL
            -- Truck BDNs awaiting approval
            SELECT 'bdn_pending', tb.created_at, tb.truck_bdn_number, o.id,
                   o.operation_number, 'truck'
            FROM truck_bdns tb JOIN operations o ON o.id = tb.operation_id
            WHERE tb.status = 'pending' AND o.deleted_at IS NULL

            UNION ALL
            -- Truck readiness feedback awaiting review
            SELECT 'feedback_pending', f.submitted_at, NULL, o.id,
                   o.operation_number, NULL
            FROM truck_feedback f JOIN operations o ON o.id = f.operation_id
            WHERE f.status = 'pending' AND o.deleted_at IS NULL

            UNION ALL
            -- Client emails approved but never actually sent
            SELECT 'unsent_email', p.approved_at, NULL, o.id,
                   o.operation_number, NULL
            FROM pending_client_notifications p
            JOIN operations o ON o.id = p.operation_id
            WHERE p.status = 'approved' AND p.sent_log_id IS NULL
              AND o.deleted_at IS NULL

            UNION ALL
            -- Safety audits returned not satisfactory, on live operations
            SELECT 'safety_audit', a.conducted_at, t.truck_number, o.id,
                   o.operation_number, NULL
            FROM truck_safety_audits a
            JOIN operations o ON o.id = a.operation_id
            JOIN trucks t ON t.id = a.truck_id
            WHERE a.result = 'not_satisfactory' AND o.deleted_at IS NULL
              AND o.status::text NOT IN ('completed','cancelled','archived')

            UNION ALL
            -- Trucks that lost more than the cap, on live operations.
            -- quantity_* columns hold LITRES despite the _mt suffix
            -- (see KPI-WORKLOG.md §5).
            --
            -- Scoped to operations still running on purpose. This feed is
            -- about what the BM can still act on: a truck that ran over the
            -- cap on a job closed seven months ago is history, and letting
            -- those in buried every pending BDN under 60 rows of the past.
            -- Closed-out losses are still fully reported, in Loss Watch and
            -- the fleet league.
            --
            -- Rolled up to ONE row per operation, not one per truck. Listed
            -- individually they were 39 near-identical rows that pushed every
            -- pending approval off the screen — the feed reported the same
            -- fact 39 times and buried the things the BM could actually act
            -- on. Per-truck detail is in the fleet league and on the
            -- operation itself.
            SELECT 'truck_loss', max(tr.discharge_end_at), count(*)::text, o.id,
                   o.operation_number,
                   round(max(abs(tr.quantity_loaded_mt - tr.quantity_discharged_mt)))::text
            FROM truck_operations tr
            JOIN operations o ON o.id = tr.operation_id
            WHERE o.deleted_at IS NULL
              AND o.status::text NOT IN ('completed','cancelled','archived')
              AND tr.quantity_loaded_mt IS NOT NULL
              AND tr.quantity_discharged_mt IS NOT NULL
              AND abs(tr.quantity_loaded_mt - tr.quantity_discharged_mt) > :cap
            GROUP BY o.id, o.operation_number
        """)
        try:
            rows = await _fetch_all(db, degraded, sql, {"cap": cap})
        except Exception as exc:
            _note_failure(degraded, "attention", exc)
            return []

        items: List[AttentionItem] = []
        for r in rows:
            kind = r["kind"]
            age = _hours_since(r["since"], now)
            op_no = r["operation_number"]

            if kind == "bdn_pending":
                is_truck = r["extra"] == "truck"
                label = "Truck BDN" if is_truck else "Vessel BDN"
                items.append(AttentionItem(
                    kind=kind,
                    severity="critical" if (age or 0) > 12 else "warning",
                    title=f"{label} {r['ref'] or ''} awaiting your approval".replace("  ", " "),
                    subtitle=op_no,
                    operation_id=r["operation_id"], operation_number=op_no,
                    since=r["since"], age_hours=age,
                ))
            elif kind == "feedback_pending":
                items.append(AttentionItem(
                    kind=kind,
                    severity="critical" if (age or 0) > 12 else "warning",
                    title="Truck readiness feedback awaiting review",
                    subtitle=op_no,
                    operation_id=r["operation_id"], operation_number=op_no,
                    since=r["since"], age_hours=age,
                ))
            elif kind == "unsent_email":
                items.append(AttentionItem(
                    kind=kind, severity="info",
                    title="Client email approved but not sent",
                    subtitle=op_no,
                    operation_id=r["operation_id"], operation_number=op_no,
                    since=r["since"], age_hours=age,
                ))
            elif kind == "safety_audit":
                items.append(AttentionItem(
                    kind=kind, severity="critical",
                    title=f"Safety audit not satisfactory — {r['ref']}",
                    subtitle=op_no,
                    operation_id=r["operation_id"], operation_number=op_no,
                    since=r["since"], age_hours=age,
                ))
            elif kind == "truck_loss":
                count = int(r["ref"] or 0)
                worst = r["extra"]
                # Warning, not critical: the fuel is already gone, so this is
                # something to investigate rather than a decision waiting on
                # the BM. Genuine action items outrank it.
                items.append(AttentionItem(
                    kind=kind, severity="warning",
                    title=(f"{count} trucks over the loss cap"
                           if count != 1 else "1 truck over the loss cap"),
                    subtitle=f"{op_no} · worst {worst} L against a {cap:,.0f} L cap",
                    operation_id=r["operation_id"], operation_number=op_no,
                    since=r["since"], age_hours=age,
                ))

        order = {"critical": 0, "warning": 1, "info": 2}
        items.sort(key=lambda i: (order.get(i.severity, 3), -(i.age_hours or 0)))
        return items

    @staticmethod
    def _stalled(live: List[LiveOperation]) -> List[AttentionItem]:
        """Operations that have gone quiet.

        Derived from the live-operations pass, which already had to work out
        each operation's last event — so this costs no extra query.
        """
        out: List[AttentionItem] = []
        for op in live:
            if op.idle_hours is None or op.idle_hours < STALL_HOURS:
                continue
            days = op.idle_hours / 24
            out.append(AttentionItem(
                kind="stalled",
                severity="critical" if op.idle_hours >= STALL_HOURS * 3 else "warning",
                title=f"{op.operation_number} has had no activity for {days:.0f} days"
                      if days >= 2 else
                      f"{op.operation_number} has had no activity for {op.idle_hours:.0f} hours",
                subtitle=(op.status or "").replace("_", " "),
                operation_id=op.operation_id,
                operation_number=op.operation_number,
                since=op.last_event_at,
                age_hours=op.idle_hours,
            ))
        return out

    # ── live operations ───────────────────────────────────────────────────
    @staticmethod
    async def _live_operations(db: AsyncSession, now: datetime,
                               degraded: List[str]) -> List[LiveOperation]:
        """Active operations and how long each has been quiet.

        Last-event time is the newest of several unrelated tables, so it is
        computed with GREATEST over correlated subqueries in one statement
        rather than by walking each operation in Python.
        """
        # Last-event time is the newest across four unrelated tables. Each
        # source is rolled up ONCE in a CTE and joined, rather than being a
        # correlated subquery evaluated per operation — that rewrite took this
        # panel from 4.7s to roughly a single round trip, and it was the
        # slowest thing on the dashboard by a wide margin.
        sql = text("""
            WITH live AS (
                SELECT id, operation_number, type::text AS type,
                       status::text AS status, created_at, updated_at
                FROM operations
                WHERE deleted_at IS NULL
                  AND status::text NOT IN
                      ('completed','cancelled','archived','draft')
            ),
            hist AS (
                SELECT operation_id, max(created_at) AS at
                FROM operation_status_history
                WHERE operation_id IN (SELECT id FROM live) GROUP BY 1
            ),
            trucks AS (
                SELECT operation_id, max(updated_at) AS at
                FROM truck_operations
                WHERE operation_id IN (SELECT id FROM live) GROUP BY 1
            ),
            va AS (
                SELECT operation_id, max(created_at) AS at
                FROM vessel_activities
                WHERE operation_id IN (SELECT id FROM live) GROUP BY 1
            )
            SELECT l.id, l.operation_number, l.type, l.status,
                   GREATEST(
                     l.updated_at,
                     COALESCE(h.at, l.created_at),
                     COALESCE(t.at, l.created_at),
                     COALESCE(v.at, l.created_at)
                   ) AS last_event_at
            FROM live l
            LEFT JOIN hist   h ON h.operation_id = l.id
            LEFT JOIN trucks t ON t.operation_id = l.id
            LEFT JOIN va     v ON v.operation_id = l.id
            ORDER BY last_event_at ASC
            LIMIT 40
        """)
        try:
            rows = await _fetch_all(db, degraded, sql)
        except Exception as exc:
            _note_failure(degraded, "live_operations", exc)
            return []

        return [
            LiveOperation(
                operation_id=r["id"],
                operation_number=r["operation_number"],
                operation_type=r["type"],
                status=r["status"],
                last_event_at=r["last_event_at"],
                idle_hours=_hours_since(r["last_event_at"], now),
            )
            for r in rows
        ]

    # ── volume pulse ──────────────────────────────────────────────────────
    @staticmethod
    async def _volume(db: AsyncSession, this_start, last_start, last_end,
                      degraded: List[str]) -> VolumePulse:
        # Scalars and the product breakdown in one round trip — the breakdown
        # comes back as JSON rather than costing a second query.
        sql = text("""
            SELECT
              (SELECT sum(COALESCE(received_mt_vacuum, discharge_mt_vacuum))
                 FROM bdns WHERE status = 'approved' AND created_at >= :ts) AS mt_this,
              (SELECT sum(COALESCE(received_mt_vacuum, discharge_mt_vacuum))
                 FROM bdns WHERE status = 'approved'
                  AND created_at >= :ls AND created_at <= :le) AS mt_last,
              (SELECT sum(quantity_discharged_mt) FROM truck_operations
                 WHERE quantity_discharged_mt IS NOT NULL
                   AND created_at >= :ts) AS l_this,
              (SELECT sum(quantity_discharged_mt) FROM truck_operations
                 WHERE quantity_discharged_mt IS NOT NULL
                   AND created_at >= :ls AND created_at <= :le) AS l_last,
              (SELECT json_agg(row_to_json(x)) FROM (
                   SELECT COALESCE(product_type,'Unspecified') AS product_type,
                          sum(COALESCE(received_mt_vacuum, discharge_mt_vacuum)) AS mt
                   FROM bdns
                   WHERE status = 'approved' AND created_at >= :ts
                   GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT 8
               ) x) AS products
        """)
        try:
            r = await _fetch_one(db, degraded, sql, {"ts": this_start, "ls": last_start, "le": last_end})
            products = r["products"] or []
        except Exception as exc:
            _note_failure(degraded, "volume", exc)
            return VolumePulse()

        this_month = _f(r["mt_this"])

        # Sanity-check the unit before calling anything MT(vac). The barge
        # measures its capacity in the low thousands of tonnes, so a monthly
        # total in the hundreds of thousands cannot be tonnes — in production
        # these columns hold litres-scale values despite the `_mt_vacuum`
        # name. Publishing "2,485,379 MT delivered" on the BM's dashboard
        # would be confidently wrong, which is the one thing a dashboard must
        # never be. Flag it and let the UI drop the unit label.
        suspect = bool(this_month and this_month > 50_000)
        if suspect:
            degraded.append("volume_units")

        return VolumePulse(
            mt_delivered_this_month=this_month,
            mt_delivered_last_month=_f(r["mt_last"]),
            litres_trucked_this_month=_f(r["l_this"]),
            litres_trucked_last_month=_f(r["l_last"]),
            vessel_figures_suspect=suspect,
            vessel_unit_label="as recorded" if suspect else "MT(vac)",
            by_product=[
                ProductVolume(product_type=p["product_type"], mt_vacuum=_f(p["mt"]))
                for p in products if p.get("mt") is not None
            ],
        )

    # ── loss watch ────────────────────────────────────────────────────────
    @staticmethod
    async def _loss(db: AsyncSession, this_start, cap: float,
                    degraded: _Ctx, price: Optional[float] = None,
                    price_basis: Optional[str] = None) -> LossWatch:
        # Only trucks carrying BOTH figures are counted — see the paired-sums
        # note in KPI-WORKLOG.md §5.
        sql = text("""
            SELECT sum(quantity_loaded_mt) AS loaded,
                   sum(quantity_discharged_mt) AS discharged,
                   avg(abs(quantity_loaded_mt - quantity_discharged_mt)) AS avg_loss,
                   sum(abs(quantity_loaded_mt - quantity_discharged_mt)) AS total_lost,
                   count(*) AS measured,
                   count(*) FILTER (
                     WHERE abs(quantity_loaded_mt - quantity_discharged_mt) > :cap
                   ) AS over_cap,
                   (SELECT json_agg(row_to_json(w)) FROM (
                        SELECT o.id, o.operation_number,
                               sum(tr.quantity_loaded_mt) AS loaded,
                               sum(tr.quantity_discharged_mt) AS discharged
                        FROM truck_operations tr
                        JOIN operations o ON o.id = tr.operation_id
                        WHERE o.deleted_at IS NULL
                          AND tr.quantity_loaded_mt IS NOT NULL
                          AND tr.quantity_discharged_mt IS NOT NULL
                          AND tr.created_at >= :ts
                        GROUP BY o.id, o.operation_number
                        HAVING sum(tr.quantity_loaded_mt) > 0
                        ORDER BY (sum(tr.quantity_loaded_mt) - sum(tr.quantity_discharged_mt))
                                 / sum(tr.quantity_loaded_mt) DESC
                        LIMIT 5
                    ) w) AS worst
            FROM truck_operations
            WHERE quantity_loaded_mt IS NOT NULL
              AND quantity_discharged_mt IS NOT NULL
              AND created_at >= :ts
        """)
        try:
            r = await _fetch_one(db, degraded, sql, {"ts": this_start, "cap": cap})
            worst = r["worst"] or []
        except Exception as exc:
            _note_failure(degraded, "loss", exc)
            return LossWatch(loss_cap_litres=cap)

        loaded, discharged = _f(r["loaded"]), _f(r["discharged"])
        pct = None
        if loaded and discharged is not None and loaded != 0:
            pct = abs(loaded - discharged) / abs(loaded) * 100.0

        rows: List[WorstOperation] = []
        for w in worst:
            wl, wd = _f(w["loaded"]), _f(w["discharged"])
            if not wl:
                continue
            rows.append(WorstOperation(
                operation_id=w["id"], operation_number=w["operation_number"],
                loss_pct=abs(wl - wd) / abs(wl) * 100.0,
                litres_lost=abs(wl - wd),
            ))

        litres_lost = _f(r["total_lost"])
        return LossWatch(
            truck_loss_pct_this_month=pct,
            avg_litres_lost_per_truck=_f(r["avg_loss"]),
            loss_cap_litres=cap,
            trucks_over_cap=int(r["over_cap"] or 0),
            trucks_measured=int(r["measured"] or 0),
            worst_operations=rows,
            litres_lost_this_month=litres_lost,
            naira_lost_estimate=(litres_lost * price) if (litres_lost and price) else None,
            price_per_litre=price,
            price_basis=price_basis,
        )

    # ── what the loss is worth ────────────────────────────────────────────
    @staticmethod
    async def _price_per_litre(db: AsyncSession, ctx: _Ctx):
        """A defensible NGN-per-litre rate, or nothing at all.

        PFIs carry an amount and a quantity, so the implied rate is
        amount / quantity_litres. In production that ranges from 0.39 to
        905,000 NGN per litre, because a good number of PFIs have the amount
        or the quantity entered in the wrong scale. Averaging that gives
        roughly 19,890 NGN per litre and a loss figure off by more than a
        factor of ten.

        So: take the MEDIAN of only those rates that could actually be a fuel
        price, and report how many were excluded. A median ignores the
        outliers that survive the filter, and the exclusion count tells the
        BM their PFI data needs attention. If too few rates are plausible,
        return nothing rather than guess — no figure beats a wrong one.
        """
        sql = text("""
            SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY rate) AS median_rate,
                   count(*) FILTER (WHERE rate BETWEEN :lo AND :hi) AS usable,
                   count(*) AS total
            FROM (SELECT amount / nullif(quantity_litres, 0) AS rate
                  FROM pfis
                  WHERE quantity_litres > 0 AND amount > 0
                    AND currency = 'NGN') x
            WHERE rate BETWEEN :lo AND :hi
        """)
        try:
            # A fuel price in Naira per litre plausibly sits somewhere in the
            # hundreds to low thousands. Deliberately wide.
            r = await _fetch_one(db, ctx, sql, {"lo": 100, "hi": 5000})
        except Exception as exc:
            logger.info("Command Center: no price basis (%s)", exc)
            return None, None, 0

        if not r or r["median_rate"] is None or (r["usable"] or 0) < 5:
            return None, None, (r["usable"] or 0) if r else 0
        return _f(r["median_rate"]), int(r["usable"]), int(r["usable"])

    # ── vendor league ─────────────────────────────────────────────────────
    @staticmethod
    async def _vendors(db: AsyncSession, cap: float, degraded: _Ctx) -> List[VendorLeagueRow]:
        """Which truck vendors actually perform.

        The Truck Operations Manager document asks for a "vendor performance
        assessment program". This is that, as a by-product of data already
        being recorded — so the next time a vendor is nominated, their history
        is one glance away.
        """
        sql = text("""
            SELECT vendor_name,
                   count(*) AS trips,
                   count(DISTINCT truck_id) AS trucks,
                   avg(abs(quantity_loaded_mt - quantity_discharged_mt)) AS avg_loss,
                   count(*) FILTER (
                     WHERE abs(quantity_loaded_mt - quantity_discharged_mt) > :cap
                   ) AS over_cap
            FROM truck_operations
            WHERE vendor_name IS NOT NULL AND btrim(vendor_name) <> ''
              AND quantity_loaded_mt IS NOT NULL
              AND quantity_discharged_mt IS NOT NULL
            GROUP BY vendor_name
            HAVING count(*) >= 2
            ORDER BY avg(abs(quantity_loaded_mt - quantity_discharged_mt)) DESC
            LIMIT 10
        """)
        try:
            rows = await _fetch_all(db, degraded, sql, {"cap": cap})
        except Exception as exc:
            _note_failure(degraded, "vendors", exc)
            return []
        out: List[VendorLeagueRow] = []
        for r in rows:
            trips = int(r["trips"] or 0)
            over = int(r["over_cap"] or 0)
            out.append(VendorLeagueRow(
                vendor_name=r["vendor_name"],
                trips=trips,
                trucks=int(r["trucks"] or 0),
                avg_litres_lost=_f(r["avg_loss"]),
                over_cap_trips=over,
                over_cap_pct=(over / trips * 100.0) if trips else None,
            ))
        return out

    # ── fleet league ──────────────────────────────────────────────────────
    @staticmethod
    async def _fleet(db: AsyncSession, cap: float, degraded: List[str]) -> List[FleetLeagueRow]:
        sql = text("""
            SELECT t.id, t.truck_number, count(*) AS trips,
                   avg(abs(tr.quantity_loaded_mt - tr.quantity_discharged_mt)) AS avg_loss,
                   avg(EXTRACT(EPOCH FROM (tr.discharge_end_at - tr.discharge_start_at))/3600.0)
                     FILTER (WHERE tr.discharge_end_at > tr.discharge_start_at) AS avg_disch,
                   count(*) FILTER (
                     WHERE abs(tr.quantity_loaded_mt - tr.quantity_discharged_mt) > :cap
                   ) AS over_cap
            FROM truck_operations tr
            JOIN trucks t ON t.id = tr.truck_id
            WHERE tr.quantity_loaded_mt IS NOT NULL
              AND tr.quantity_discharged_mt IS NOT NULL
            GROUP BY t.id, t.truck_number
            HAVING count(*) >= 2
            ORDER BY avg(abs(tr.quantity_loaded_mt - tr.quantity_discharged_mt)) DESC
            LIMIT 10
        """)
        try:
            rows = await _fetch_all(db, degraded, sql, {"cap": cap})
        except Exception as exc:
            _note_failure(degraded, "fleet", exc)
            return []
        return [
            FleetLeagueRow(
                truck_id=r["id"], truck_number=r["truck_number"],
                trips=int(r["trips"] or 0),
                avg_litres_lost=_f(r["avg_loss"]),
                avg_discharge_hours=_f(r["avg_disch"]),
                over_cap_trips=int(r["over_cap"] or 0),
            )
            for r in rows
        ]

    # ── licence runway ────────────────────────────────────────────────────
    @staticmethod
    async def _licences(db: AsyncSession, degraded: List[str]) -> List[LicenceRunway]:
        """Days of cover left per product on the current PPDL.

        Balances are computed on read here exactly as the licence module does
        it — the PPDL product line minus every BFL drawn against it. Burn rate
        is BFL creation over the last 90 days, which is the drawdown that
        actually consumes the licence.
        """
        sql = text("""
            WITH current_ppdl AS (
                SELECT id FROM ppdls WHERE is_current LIMIT 1
            ),
            allocated AS (
                SELECT product_type, sum(quantity_litres) AS drawn
                FROM bfls
                WHERE is_active AND ppdl_id = (SELECT id FROM current_ppdl)
                GROUP BY product_type
            ),
            burn AS (
                SELECT product_type, sum(quantity_litres) AS litres_90d
                FROM bfls
                WHERE ppdl_id = (SELECT id FROM current_ppdl)
                  AND created_at >= now() - interval '90 days'
                GROUP BY product_type
            )
            SELECT p.product_type,
                   p.quantity_litres - COALESCE(a.drawn, 0) AS remaining,
                   COALESCE(b.litres_90d, 0) / 3.0 AS monthly
            FROM ppdl_products p
            LEFT JOIN allocated a ON a.product_type = p.product_type
            LEFT JOIN burn b ON b.product_type = p.product_type
            WHERE p.ppdl_id = (SELECT id FROM current_ppdl)
            ORDER BY p.product_type
        """)
        try:
            rows = await _fetch_all(db, degraded, sql)
        except Exception as exc:
            _note_failure(degraded, "licences", exc)
            return []

        out: List[LicenceRunway] = []
        for r in rows:
            remaining = _f(r["remaining"])
            monthly = _f(r["monthly"])
            days = None
            severity = "ok"
            if remaining is not None and monthly:
                days = remaining / monthly * 30.0
                if days <= RUNWAY_CRITICAL_DAYS:
                    severity = "critical"
                elif days <= RUNWAY_WARNING_DAYS:
                    severity = "warning"
            elif remaining is not None and remaining <= 0:
                severity = "critical"
            out.append(LicenceRunway(
                product_type=r["product_type"],
                remaining_litres=remaining,
                avg_monthly_litres=monthly,
                days_left=days,
                severity=severity,
            ))
        return out

    # ── money strip ───────────────────────────────────────────────────────
    @staticmethod
    async def _money(db: AsyncSession, this_start, degraded: List[str]) -> MoneyStrip:
        sql = text("""
            SELECT
              (SELECT sum(amount) FROM payments WHERE payment_date >= :ts) AS collected,
              (SELECT sum(total_amount) FROM invoices
                 WHERE status::text NOT IN ('paid','cancelled')) AS outstanding,
              (SELECT count(*) FROM invoices
                 WHERE status::text NOT IN ('paid','cancelled')) AS outstanding_n,
              (SELECT count(*) FROM vouchers WHERE status::text = 'submitted') AS vouchers
        """)
        try:
            r = await _fetch_one(db, degraded, sql, {"ts": this_start})
        except Exception as exc:
            _note_failure(degraded, "money", exc)
            return MoneyStrip()
        return MoneyStrip(
            collected_this_month=_f(r["collected"]),
            outstanding_invoices=_f(r["outstanding"]),
            invoices_outstanding_count=int(r["outstanding_n"] or 0),
            vouchers_pending=int(r["vouchers"] or 0),
        )

    # ── team pulse (depends on Phase 2) ───────────────────────────────────
    @staticmethod
    async def _team(db: AsyncSession, degraded: _Ctx, available: bool) -> TeamPulse:
        """Average score per role, from Phase 2's monthly snapshots.

        Phase 2 owns `kpi_snapshots` and may not have built it yet. That is a
        normal state, not an error: report it plainly so the UI can say
        "trends available once monthly scoring runs" instead of drawing an
        empty chart that reads as a team scoring zero.
        """
        _unavailable = TeamPulse(
            available=False,
            unavailable_reason="Monthly scoring has not run yet — role trends appear once Phase 2's snapshots exist",
        )
        if not available:
            return _unavailable
        try:
            rows = await _fetch_all(db, degraded, text("""
                SELECT period, subject_role, avg_score
                FROM kpi_role_scores_current
            """))
        except Exception:
            return _unavailable

        roles: List[RolePulse] = []
        period = None
        for r in rows:
            period = r["period"]
            try:
                role = UserRole(r["subject_role"])
                label = role_label(role)
            except ValueError:
                label = str(r["subject_role"])
            score = _f(r["avg_score"])
            roles.append(RolePulse(
                role=str(r["subject_role"]), role_label=label,
                score=score, rating=rating_for(score),
            ))
        # An empty result is NOT "available with nothing in it". The view
        # returns no rows until a month has actually been closed, and calling
        # that available makes the UI say "no scores recorded for this period"
        # — which reads as "everyone scored nothing" rather than "this has
        # never been run". Same distinction the rest of the system keeps
        # between a measured zero and an absent figure.
        if not roles:
            return _unavailable
        return TeamPulse(available=True, period=period, roles=roles)
