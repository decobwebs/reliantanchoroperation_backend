"""The daily digest — what needs the Bunker Manager, delivered to him.

Why this exists. The Command Center knows that a delivery note disagrees with
the system by ₦39M, that a job has gone quiet for three days, that a licence is
running out. None of that reaches anybody. It waits on the screen until someone
remembers to look.

A dashboard nobody opens is a filing cabinet.

TWO SAFETY RULES, both deliberate:

1. **It only ever sends to the person asking for it.** The recipient is taken
   from the authenticated user, never from a parameter and never from a list.
   There is no way to make this mail somebody else, so it cannot become a way
   to blast scores at staff.

2. **Nothing sends on a schedule.** Sending is an explicit act. Automatic
   delivery would need its own decision, because email from this system reaches
   real inboxes the moment the API key is set.

The digest reuses the Command Center's own computation rather than asking the
same questions differently — one source of truth means the email and the screen
can never disagree.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.kpi.command_service import CommandCenterService
from app.kpi.schemas_command import CommandCenterOut
from app.services.email_service import _esc, _wrap_email, send_email

logger = logging.getLogger("raoms")

_INK = "#1F2937"
_MUTED = "#5B6472"
_BORDER = "#E7EAEE"

_SEVERITY_COLOUR = {
    "critical": "#B3403A",
    "warning": "#A66A00",
    "info": "#0B5D6B",
}


def _age(hours: Optional[float]) -> str:
    if hours is None:
        return ""
    if hours < 1:
        return f"{round(hours * 60)}m"
    if hours < 48:
        return f"{round(hours)}h"
    return f"{round(hours / 24)}d"


def _num(value: Optional[float], dp: int = 0) -> str:
    """Absent renders blank — the same rule the screens follow. A missing
    figure must never appear as a confident zero in something a manager reads
    over breakfast."""
    if value is None:
        return ""
    return f"{value:,.{dp}f}"


def _attention_rows(cc: CommandCenterOut, limit: int = 12) -> str:
    if not cc.attention:
        return (
            f'<p style="margin:0;color:{_MUTED};font-size:14px;'
            f'font-family:Helvetica,Arial,sans-serif;">'
            "Nothing is waiting on you. Every approval is clear.</p>"
        )

    rows = []
    for item in cc.attention[:limit]:
        colour = _SEVERITY_COLOUR.get(item.severity, _MUTED)
        age = _age(item.age_hours)
        subtitle = (
            f'<div style="color:{_MUTED};font-size:12px;padding-top:2px;">'
            f"{_esc(item.subtitle)}</div>"
            if item.subtitle else ""
        )
        rows.append(f"""
        <tr>
          <td style="padding:9px 0;border-bottom:1px solid {_BORDER};
                     border-left:3px solid {colour};padding-left:10px;
                     font-family:Helvetica,Arial,sans-serif;">
            <div style="color:{_INK};font-size:13.5px;font-weight:600;">
              {_esc(item.title)}</div>
            {subtitle}
          </td>
          <td style="padding:9px 0 9px 10px;border-bottom:1px solid {_BORDER};
                     text-align:right;vertical-align:top;color:{colour};
                     font-size:12px;font-family:Helvetica,Arial,sans-serif;
                     white-space:nowrap;">{_esc(age)}</td>
        </tr>""")

    more = ""
    if cc.attention_total > limit:
        more = (
            f'<p style="margin:10px 0 0;color:{_MUTED};font-size:12.5px;'
            f'font-family:Helvetica,Arial,sans-serif;">'
            f"and {cc.attention_total - limit} more on the Command Center</p>"
        )

    return (
        '<table width="100%" cellpadding="0" cellspacing="0" role="presentation">'
        + "".join(rows)
        + "</table>"
        + more
    )


def _pulse_block(cc: CommandCenterOut) -> str:
    loss = cc.loss
    facts: List[tuple] = []

    if loss.avg_litres_lost_per_truck is not None:
        cap = (
            f" (cap {_num(loss.loss_cap_litres)} L)"
            if loss.loss_cap_litres is not None else ""
        )
        facts.append((
            "Average loss per truck",
            f"{_num(loss.avg_litres_lost_per_truck)} L{cap}",
        ))
    if loss.trucks_measured:
        facts.append((
            "Trucks over the cap",
            f"{loss.trucks_over_cap} of {loss.trucks_measured}",
        ))
    if loss.naira_lost_estimate is not None:
        facts.append((
            "Estimated value of loss",
            f"₦{_num(loss.naira_lost_estimate)}",
        ))
    facts.append(("Live operations", str(len(cc.live_operations))))

    if not facts:
        return ""

    rows = "".join(f"""
        <tr>
          <td style="padding:6px 0;color:{_MUTED};font-size:12.5px;width:55%;
                     font-family:Helvetica,Arial,sans-serif;">{_esc(k)}</td>
          <td style="padding:6px 0;color:{_INK};font-size:13px;font-weight:600;
                     text-align:right;font-family:Helvetica,Arial,sans-serif;">
            {_esc(v)}</td>
        </tr>""" for k, v in facts)

    return f"""
    <div style="margin-top:26px;">
      <div style="color:{_INK};font-size:14px;font-weight:700;padding-bottom:6px;
                  font-family:Helvetica,Arial,sans-serif;">This month so far</div>
      <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
             style="border:1px solid {_BORDER};border-radius:6px;padding:8px 12px;">
        {rows}
      </table>
    </div>"""


def _caveats(cc: CommandCenterOut) -> str:
    """Anything the figures above cannot be trusted on. A digest that quietly
    hides a known data problem is worse than one that never ran."""
    if not cc.data_warnings:
        return ""
    items = "".join(
        f'<li style="margin:4px 0;">{_esc(w)}</li>' for w in cc.data_warnings
    )
    return f"""
    <div style="margin-top:22px;border-left:3px solid #A66A00;
                background:#FAF0DC;padding:10px 14px;border-radius:0 6px 6px 0;">
      <div style="color:#8A5A00;font-size:11px;font-weight:700;
                  text-transform:uppercase;letter-spacing:.08em;
                  font-family:Helvetica,Arial,sans-serif;">Read with care</div>
      <ul style="margin:6px 0 0;padding-left:18px;color:{_INK};font-size:12.5px;
                 font-family:Helvetica,Arial,sans-serif;">{items}</ul>
    </div>"""


class DigestService:

    @staticmethod
    async def build(db: AsyncSession) -> Dict:
        """Compose the digest. Read-only — computing it sends nothing."""
        cc = await CommandCenterService.build(db)

        critical = sum(1 for a in cc.attention if a.severity == "critical")
        if cc.attention_total == 0:
            subject = "RAOMS — nothing waiting on you"
        elif critical:
            subject = (
                f"RAOMS — {critical} urgent item{'s' if critical != 1 else ''} "
                f"need you"
            )
        else:
            subject = f"RAOMS — {cc.attention_total} items waiting"

        heading = (
            "Nothing is waiting on you"
            if cc.attention_total == 0
            else f"{cc.attention_total} thing"
                 f"{'s' if cc.attention_total != 1 else ''} need your attention"
        )

        body = f"""
        <p style="margin:0 0 16px;color:{_MUTED};font-size:14px;line-height:1.55;
                  font-family:Helvetica,Arial,sans-serif;">
          {_esc(heading)}. Oldest and most urgent first.
        </p>
        {_attention_rows(cc)}
        {_pulse_block(cc)}
        {_caveats(cc)}"""

        html = _wrap_email(
            title="What needs you today",
            body_html=body,
            cta_label="Open the Command Center",
            cta_url=f"{settings.FRONTEND_URL.rstrip('/')}/command-center",
            preheader=subject,
        )

        # A plain-text fallback, for clients that refuse HTML.
        #
        # The subtitle carries the operation number, so dropping it turns four
        # separate problems into four identical-looking lines ("3 trucks over
        # the loss cap") that nobody can act on. Include it.
        lines = [heading, ""]
        for a in cc.attention[:12]:
            age = _age(a.age_hours)
            lines.append(f"[{a.severity}] {a.title}" + (f"  ({age})" if age else ""))
            if a.subtitle:
                lines.append(f"    {a.subtitle}")
        if cc.attention_total > 12:
            lines.append(f"...and {cc.attention_total - 12} more")
        text = "\n".join(lines)

        return {
            "subject": subject,
            "html": html,
            "text": text,
            "attention_total": cc.attention_total,
            "critical_total": critical,
            "generated_at": datetime.now(timezone.utc),
            "degraded": cc.degraded,
            "data_warnings": cc.data_warnings,
        }

    @staticmethod
    async def send_to_self(db: AsyncSession, recipient_email: str) -> Dict:
        """Send the digest to ONE address — the caller's own.

        The address is passed in from the authenticated user by the route.
        There is no parameter for it and no list, so this cannot be pointed at
        anybody else.
        """
        digest = await DigestService.build(db)
        sent = await send_email(
            to=[recipient_email],
            subject=digest["subject"],
            html_body=digest["html"],
            text_body=digest["text"],
        )
        logger.info(
            "KPI digest %s to %s (%s items)",
            "sent" if sent else "suppressed", recipient_email,
            digest["attention_total"],
        )
        return {
            "sent": sent,
            "recipient": recipient_email,
            "subject": digest["subject"],
            "attention_total": digest["attention_total"],
            "critical_total": digest["critical_total"],
            # False when RESEND_API_KEY is unset — the caller should say so
            # rather than claiming a delivery that never happened.
            "email_configured": bool(settings.RESEND_API_KEY),
        }
