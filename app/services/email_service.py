"""
Email service via Resend API.
Gracefully degrades (logs warning) when RESEND_API_KEY is not configured.

All outbound email shares one branded HTML wrapper (_wrap_email) so every
message — task assignment, PFI notice, account creation, password reset —
looks like it comes from the same, deliberately formal system.
"""
import html
import logging
from datetime import datetime, timezone
from typing import List, Optional

import httpx

from app.config import settings

logger = logging.getLogger("raoms.email")


def _esc(value) -> str:
    """HTML-escape a value before interpolating it into an email body."""
    return html.escape(str(value)) if value is not None else ""


RESEND_API_URL = "https://api.resend.com/emails"

# Brand colors — mirrors the app's navy/azure theme (globals.css) and the
# invoice/PFI PDF header (app/utils/invoice_pdf.py) so every client touchpoint
# — app, PDF, email — reads as one system.
# Sampled from public/logo-mark.png — the logo contains exactly two colours.
# These are the same values globals.css uses for --navy-900 / --brand-500.
_NAVY = "#102447"
_NAVY_MUTED = "#A2B4D5"
_AZURE = "#0E79C8"
_INK = "#1F2937"
_MUTED = "#5B6472"
_BORDER = "#E7EAEE"
_BG = "#F1F4F8"


def _logo_url() -> str:
    """The anchor mark, cropped and downscaled for email (~16 KB).

    Deliberately not logo-mark.png — that is the full 879 KB lockup, far too
    heavy for an email header and illegible at 46px once the wordmark is
    included.
    """
    return f"{settings.FRONTEND_URL.rstrip('/')}/logo-email.png"


def _detail_rows(pairs: List[tuple]) -> str:
    """A bordered label/value panel — for the facts a recipient needs to
    confirm at a glance (which account, which role). Table-based so it holds
    up in Outlook, which ignores flex/grid entirely."""
    rows = "".join(
        f"""
        <tr>
          <td style="padding:7px 0;color:{_MUTED};font-size:12.5px;width:38%;
                     font-family:Helvetica,Arial,sans-serif;vertical-align:top;">{_esc(k)}</td>
          <td style="padding:7px 0;color:{_INK};font-size:13px;font-weight:600;
                     font-family:Helvetica,Arial,sans-serif;">{_esc(v)}</td>
        </tr>"""
        for k, v in pairs
    )
    return f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
             style="background:{_BG};border:1px solid {_BORDER};border-radius:8px;
                    padding:6px 16px;margin:18px 0 6px;">
        {rows}
      </table>"""


def _callout(text: str, tone: str = "amber") -> str:
    """A single tinted line for the one thing that must not be missed —
    typically a link expiry."""
    bg, border, ink = {
        "amber": ("#FEF8E7", "#F6D98A", "#8A6100"),
        "azure": ("#E0EEF8", "#BEDDF3", "#0B5287"),
    }.get(tone, ("#FEF8E7", "#F6D98A", "#8A6100"))
    return f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:18px 0 0;">
        <tr><td style="background:{bg};border-left:3px solid {border};border-radius:5px;
                       padding:11px 14px;color:{ink};font-size:12.5px;line-height:1.6;
                       font-family:Helvetica,Arial,sans-serif;">
          {text}
        </td></tr>
      </table>"""


def _wrap_email(
    *,
    title: str,
    body_html: str,
    cta_label: Optional[str] = None,
    cta_url: Optional[str] = None,
    preheader: Optional[str] = None,
) -> str:
    """Wrap template-specific body content in the shared branded shell.

    Table-based layout with inline styles — the only markup that renders
    consistently across email clients (no external stylesheet, no flexbox).
    """
    year = datetime.now(timezone.utc).year
    logo = _esc(_logo_url())

    cta_block = ""
    if cta_label and cta_url:
        safe_url = _esc(cta_url)
        cta_block = f"""
          <tr><td align="center" style="padding:28px 0 4px;">
            <a href="{safe_url}"
               style="background-color:{_AZURE};color:#FFFFFF;text-decoration:none;
                      font-weight:600;font-size:14px;padding:13px 32px;border-radius:6px;
                      display:inline-block;font-family:Helvetica,Arial,sans-serif;">
              {_esc(cta_label)}
            </a>
          </td></tr>
          <tr><td style="padding:10px 0 0;">
            <p style="margin:0;color:{_MUTED};font-size:11.5px;line-height:1.6;
                      font-family:Helvetica,Arial,sans-serif;word-break:break-all;">
              If the button above doesn't work, copy and paste this link into your browser:<br>
              <span style="color:{_AZURE};">{safe_url}</span>
            </p>
          </td></tr>
        """

    preheader_html = ""
    if preheader:
        # Hidden preview text shown in inbox lists, not in the email body.
        preheader_html = (
            f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">'
            f'{_esc(preheader)}</div>'
        )

    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background-color:{_BG};font-family:Helvetica,Arial,sans-serif;">
    {preheader_html}
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:{_BG};padding:32px 16px;">
      <tr><td align="center">
        <table role="presentation" width="560" cellpadding="0" cellspacing="0"
               style="max-width:560px;width:100%;background:#FFFFFF;border-radius:10px;overflow:hidden;
                      box-shadow:0 1px 4px rgba(20,30,50,0.08);">
          <!-- Header -->
          <tr><td style="background-color:{_NAVY};padding:28px 32px;text-align:center;">
            <img src="{logo}" width="46" height="46" alt="Reliant Anchor Logistics"
                 style="display:block;margin:0 auto 10px;border-radius:8px;">
            <div style="color:#FFFFFF;font-size:15px;font-weight:700;letter-spacing:0.4px;
                        font-family:Helvetica,Arial,sans-serif;">
              RELIANT ANCHOR LOGISTICS LIMITED
            </div>
            <div style="color:{_NAVY_MUTED};font-size:10.5px;letter-spacing:1.2px;
                        text-transform:uppercase;margin-top:3px;font-family:Helvetica,Arial,sans-serif;">
              Operations Management System
            </div>
          </td></tr>

          <!-- Body -->
          <tr><td style="padding:36px 32px 28px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr><td>
                <h1 style="margin:0 0 16px;color:{_NAVY};font-size:19px;font-weight:700;
                           font-family:Helvetica,Arial,sans-serif;">
                  {_esc(title)}
                </h1>
                <div style="color:{_INK};font-size:14px;line-height:1.7;
                            font-family:Helvetica,Arial,sans-serif;">
                  {body_html}
                </div>
              </td></tr>
              {cta_block}
            </table>
          </td></tr>

          <!-- Footer -->
          <tr><td style="background-color:{_BG};padding:20px 32px;border-top:1px solid {_BORDER};">
            <p style="margin:0;color:#8A94A3;font-size:11.5px;line-height:1.6;
                      font-family:Helvetica,Arial,sans-serif;">
              This is an automated message from Reliant Anchor Operations — please do not reply
              directly to this email.<br>
              © {year} Reliant Anchor Logistics Limited. All rights reserved.
            </p>
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""


async def send_email(
    to: List[str],
    subject: str,
    html_body: str,
    text_body: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> bool:
    """Send a transactional email via Resend. Returns True on success, False if skipped/failed."""
    if not settings.RESEND_API_KEY:
        logger.warning(
            "RESEND_API_KEY not configured — email suppressed: subject=%s to=%s",
            subject, to,
        )
        return False

    payload: dict = {
        "from": f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>",
        "to": to,
        "subject": subject,
        "html": html_body,
    }
    if text_body:
        payload["text"] = text_body
    if reply_to:
        payload["reply_to"] = reply_to

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                RESEND_API_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code in (200, 201):
            logger.info("Email sent: subject=%s to=%s", subject, to)
            return True
        else:
            logger.error(
                "Resend API error %s: %s", resp.status_code, resp.text[:200]
            )
            return False
    except Exception as exc:
        logger.error("Email send failed: %s", exc)
        return False


# ── Pre-built email templates ──────────────────────────────────────────────────

async def email_account_created(
    to_email: str,
    recipient_name: str,
    role_label: str,
    set_password_url: str,
    is_new_account: bool = True,
) -> bool:
    """Sent when a Bunker Manager creates a user account. No password is ever
    set by the admin — the recipient chooses their own via this link."""
    first_name = (recipient_name or "").strip().split(" ")[0] or "there"

    if is_new_account:
        subject = f"Welcome to Reliant Anchor — set your password ({role_label})"
        title = "Welcome aboard"
        preheader = f"Your {role_label} account is ready — set a password to sign in."
        intro = (
            "An account has been created for you on the <strong>Reliant Anchor Operations "
            "Management System</strong>. Set a password below and you're ready to sign in."
        )
        details = _detail_rows([
            ("Name", recipient_name),
            ("Email", to_email),
            ("Role", role_label),
        ])
        closing = (
            "<p style=\"margin:16px 0 0;\">Once you're in, you'll find everything for your role "
            "on your dashboard. If anything looks wrong — including the role above — contact your "
            "Bunker Manager before signing in.</p>"
        )
    else:
        subject = "Reset your Reliant Anchor password"
        title = "Reset your password"
        preheader = "A password reset was requested for your Reliant Anchor account."
        intro = (
            "A password reset was requested for your <strong>Reliant Anchor Operations</strong> "
            "account. Choose a new password using the button below."
        )
        details = _detail_rows([("Email", to_email), ("Role", role_label)])
        closing = (
            "<p style=\"margin:16px 0 0;\">If you didn't request this, you can ignore this email — "
            "your password stays unchanged. Tell your Bunker Manager if you weren't expecting it.</p>"
        )

    body = f"""
      <p style="margin:0 0 14px;">Hello {_esc(first_name)},</p>
      <p style="margin:0 0 4px;">{intro}</p>
      {details}
      {_callout("This link expires in <strong>1 hour</strong> and can only be used once. "
                "If it lapses, ask your Bunker Manager to send a new invite.")}
      {closing}
    """

    # Plain-text alternative: improves deliverability, and is what screen
    # readers and text-only clients actually get.
    text_body = (
        f"Hello {first_name},\n\n"
        f"{'An account has been created for you on the Reliant Anchor Operations Management System.' if is_new_account else 'A password reset was requested for your Reliant Anchor Operations account.'}\n\n"
        f"Name:  {recipient_name}\n"
        f"Email: {to_email}\n"
        f"Role:  {role_label}\n\n"
        f"Set your password here (expires in 1 hour, single use):\n{set_password_url}\n\n"
        f"{'If anything looks wrong, including the role above, contact your Bunker Manager before signing in.' if is_new_account else 'If you did not request this, ignore this email — your password stays unchanged.'}\n\n"
        f"— Reliant Anchor Logistics Limited\n"
        f"This is an automated message; please do not reply."
    )

    return await send_email(
        [to_email], subject,
        _wrap_email(
            title=title,
            body_html=body,
            cta_label="Set your password" if is_new_account else "Choose a new password",
            cta_url=set_password_url,
            preheader=preheader,
        ),
        text_body=text_body,
    )


async def email_task_assigned(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    task_type: str,
) -> bool:
    subject = f"Task Assigned — Operation {operation_number}"
    body = f"""
      <p style="margin:0 0 14px;">Dear {_esc(recipient_name)},</p>
      <p style="margin:0 0 14px;">You have been assigned a new task on operation
      <strong>{_esc(operation_number)}</strong>:</p>
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 14px;">
        <tr>
          <td style="color:{_MUTED};font-size:13px;padding-right:10px;">Task Type</td>
          <td style="color:{_INK};font-size:13px;font-weight:600;">
            {_esc(task_type.replace('_', ' ').title())}
          </td>
        </tr>
      </table>
      <p style="margin:0;">Please log in to RAOMS to review and act on this assignment.</p>
    """
    return await send_email(
        [to_email], subject,
        _wrap_email(title="New Task Assigned", body_html=body),
    )


async def email_pfi_linked(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    pfi_number: str,
    amount: str,
    currency: str,
) -> bool:
    subject = f"PFI Ready for Payment — {pfi_number}"
    body = f"""
      <p style="margin:0 0 14px;">Dear {_esc(recipient_name)},</p>
      <p style="margin:0 0 14px;">A Pro-Forma Invoice has been linked to operation
      <strong>{_esc(operation_number)}</strong>:</p>
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 14px;width:100%;">
        <tr>
          <td style="color:{_MUTED};font-size:13px;padding:2px 0;">PFI Number</td>
          <td style="color:{_INK};font-size:13px;font-weight:600;text-align:right;">{_esc(pfi_number)}</td>
        </tr>
        <tr>
          <td style="color:{_MUTED};font-size:13px;padding:2px 0;">Amount</td>
          <td style="color:{_INK};font-size:13px;font-weight:600;text-align:right;">
            {_esc(currency)} {_esc(amount)}
          </td>
        </tr>
      </table>
      <p style="margin:0;">Please process payment at your earliest convenience.</p>
    """
    return await send_email(
        [to_email], subject,
        _wrap_email(title="Pro-Forma Invoice Ready", body_html=body),
    )


async def email_payment_confirmed(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    voucher_number: str,
) -> bool:
    subject = f"Payment Confirmed — Operation {operation_number}"
    body = f"""
      <p style="margin:0 0 14px;">Dear {_esc(recipient_name)},</p>
      <p style="margin:0 0 14px;">Payment voucher <strong>{_esc(voucher_number)}</strong> for
      operation <strong>{_esc(operation_number)}</strong> has been confirmed.</p>
      <p style="margin:0;">The operation is now cleared to proceed to vessel operations.</p>
    """
    return await send_email(
        [to_email], subject,
        _wrap_email(title="Payment Confirmed", body_html=body),
    )


def _figures_table(rows: list) -> str:
    """Renders (label, value) pairs as an email-safe table.

    A row whose value is empty is dropped entirely rather than printed as a
    dash — the BM's instruction for the on-screen readouts applies equally
    here ("just leave it blank, we know exactly what it is").

    Units belong in the value the caller passes, not here: the vessel side is
    MT(vac) and litres are a truck unit, so nothing is appended blindly the
    way a hardcoded " L" used to be.
    """
    cells = []
    for label, value in rows:
        if value is None or str(value).strip() == "":
            continue
        cells.append(
            f'<tr>'
            f'<td style="color:{_MUTED};font-size:13px;padding:2px 0;">{_esc(label)}</td>'
            f'<td style="color:{_INK};font-size:13px;font-weight:600;text-align:right;">{_esc(value)}</td>'
            f'</tr>'
        )
    if not cells:
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="margin:0 0 14px;width:100%;">' + "".join(cells) + "</table>"
    )


async def email_bdn_approved(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    bdn_number: str,
    quantity: str = "",
    *,
    gov: str = "", gsv: str = "", mt_vacuum: str = "",
    density: str = "", temperature: str = "", vcf: str = "",
    unit: str = "MT(vac)",
    vessel_name: str = "", company_name: str = "", receiving_vessel: str = "",
) -> bool:
    """Approval notice. `quantity` is the headline figure in `unit` — MT(vac)
    for anything vessel-side, litres only for a truck BDN."""
    subject = f"BDN Approved — {bdn_number}"
    figures = _figures_table([
        ("BDN Number", bdn_number),
        ("Operation", operation_number),
        ("Vessel", vessel_name),
        ("Client", company_name),
        ("Receiving Vessel", receiving_vessel),
        ("GOV", gov),
        ("GSV", gsv),
        ("MT(vac)", mt_vacuum),
        ("Density", density),
        ("Temperature", f"{temperature}°" if temperature else ""),
        ("VCF", vcf),
        ("Quantity Delivered", f"{quantity} {unit}" if quantity else ""),
    ])
    body = f"""
      <p style="margin:0 0 14px;">Dear {_esc(recipient_name)},</p>
      <p style="margin:0 0 14px;">Bunker Delivery Note <strong>{_esc(bdn_number)}</strong> for
      operation <strong>{_esc(operation_number)}</strong> has been approved.</p>
      {figures}
    """
    return await send_email(
        [to_email], subject,
        _wrap_email(title="Bunker Delivery Note Approved", body_html=body),
    )


async def email_truck_bdn_submitted(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    truck_bdn_number: str,
    quantity_loaded: str = "",
    quantity_discharged: str = "",
    *,
    gov: str = "", gsv: str = "", mt_vacuum: str = "",
    density: str = "", temperature: str = "", vcf: str = "",
    truck_number: str = "",
) -> bool:
    """Truck BDN — the one place litres stay, per the BM: everything is
    MT(vac) from the moment it is on the vessel, "except on truck, that is
    just litres"."""
    subject = f"Truck BDN Ready for Review — {truck_bdn_number}"
    figures = _figures_table([
        ("Truck BDN Number", truck_bdn_number),
        ("Operation", operation_number),
        ("Truck", truck_number),
        ("Quantity Loaded", f"{quantity_loaded} L" if quantity_loaded else ""),
        ("Quantity Discharged", f"{quantity_discharged} L" if quantity_discharged else ""),
        ("GOV", gov),
        ("GSV", gsv),
        ("MT(vac)", mt_vacuum),
        ("Density", density),
        ("Temperature", f"{temperature}°" if temperature else ""),
        ("VCF", vcf),
    ])
    body = f"""
      <p style="margin:0 0 14px;">Dear {_esc(recipient_name)},</p>
      <p style="margin:0 0 14px;">A Truck Bunker Delivery Note has been submitted for
      operation <strong>{_esc(operation_number)}</strong>:</p>
      {figures}
    """
    return await send_email(
        [to_email], subject,
        _wrap_email(title="Truck Bunker Delivery Note Submitted", body_html=body),
    )


async def email_vessel_bdn_submitted(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    vessel_bdn_number: str,
    *,
    gov: str = "", gsv: str = "", mt_vacuum: str = "",
    density: str = "", temperature: str = "", vcf: str = "",
    vessel_name: str = "", company_name: str = "", receiving_vessel: str = "",
) -> bool:
    """Vessel-side figures only — GOV/GSV/MT(vac) plus the quality readings.

    The old signature took quantity_loaded/quantity_discharged and printed
    both with " L". Those two fields were dropped from the submission form,
    after which callers were passing GOV and MT(vac) into them, so the email
    displayed correct numbers under wrong labels. Named figures now, so a
    value cannot land under the wrong heading.
    """
    subject = f"Vessel BDN Ready for Review — {vessel_bdn_number}"
    figures = _figures_table([
        ("Vessel BDN Number", vessel_bdn_number),
        ("Operation", operation_number),
        ("Vessel", vessel_name),
        ("Client", company_name),
        ("Receiving Vessel", receiving_vessel),
        ("GOV", gov),
        ("GSV", gsv),
        ("MT(vac)", mt_vacuum),
        ("Density", density),
        ("Temperature", f"{temperature}°" if temperature else ""),
        ("VCF", vcf),
    ])
    body = f"""
      <p style="margin:0 0 14px;">Dear {_esc(recipient_name)},</p>
      <p style="margin:0 0 14px;">A Vessel Bunker Delivery Note has been submitted for
      operation <strong>{_esc(operation_number)}</strong>:</p>
      {figures}
    """
    return await send_email(
        [to_email], subject,
        _wrap_email(title="Vessel Bunker Delivery Note Submitted", body_html=body),
    )


async def email_client_notification(
    to_email: str,
    recipient_name: str,
    subject: str,
    body_html: str,
) -> bool:
    """Client-facing send — isolated to a single recipient by construction
    (one call = one email, no CC/BCC ever). Always do-not-reply: the client
    is directed to their usual point of contact, never back to this address."""
    body = f"""
      <p style="margin:0 0 14px;">Dear {_esc(recipient_name)},</p>
      <p style="margin:0 0 14px;">{body_html}</p>
      <table role="presentation" cellpadding="0" cellspacing="0"
             style="margin:18px 0 0;width:100%;background-color:{_BG};border-radius:6px;">
        <tr><td style="padding:12px 14px;">
          <p style="margin:0;color:{_MUTED};font-size:12px;line-height:1.6;">
            This is an automated, do-not-reply notification. For any questions, please contact your
            usual Reliant Anchor point of contact.
          </p>
        </td></tr>
      </table>
    """
    return await send_email(
        [to_email], subject,
        _wrap_email(title=subject, body_html=body),
    )


async def email_feedback_rejected(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    reason: str,
) -> bool:
    subject = f"Truck Feedback Rejected — Operation {operation_number}"
    body = f"""
      <p style="margin:0 0 14px;">Dear {_esc(recipient_name)},</p>
      <p style="margin:0 0 14px;">Your truck readiness feedback for operation
      <strong>{_esc(operation_number)}</strong> has been rejected.</p>
      <table role="presentation" cellpadding="0" cellspacing="0"
             style="margin:0 0 14px;width:100%;background-color:{_BG};border-radius:6px;">
        <tr><td style="padding:12px 14px;">
          <div style="color:{_MUTED};font-size:11.5px;text-transform:uppercase;letter-spacing:0.4px;margin-bottom:3px;">
            Reason
          </div>
          <div style="color:{_INK};font-size:13.5px;">{_esc(reason)}</div>
        </td></tr>
      </table>
      <p style="margin:0;">Please address the issue and resubmit at your earliest convenience.</p>
    """
    return await send_email(
        [to_email], subject,
        _wrap_email(title="Truck Feedback Rejected", body_html=body),
    )
