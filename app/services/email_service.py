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
_NAVY = "#1C2E4C"
_NAVY_MUTED = "#9FB3CC"
_AZURE = "#3080C0"
_INK = "#1F2937"
_MUTED = "#5B6472"
_BORDER = "#E7EAEE"
_BG = "#F1F4F8"


def _logo_url() -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/logo.jpeg"


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
    subject = (
        "Welcome to RAOMS — Set Your Password"
        if is_new_account
        else "Your RAOMS Password Reset"
    )
    intro = (
        f"An account has been created for you on the Reliant Anchor Operations "
        f"Management System (RAOMS), with the role of <strong>{_esc(role_label)}</strong>."
        if is_new_account
        else "A password reset was requested for your RAOMS account."
    )
    body = f"""
      <p style="margin:0 0 14px;">Dear {_esc(recipient_name)},</p>
      <p style="margin:0 0 14px;">{intro}</p>
      <p style="margin:0 0 4px;">To activate your account, please set your password using the
      button below. This link is valid for <strong>1 hour</strong> and can only be used once.</p>
    """
    ok = await send_email(
        [to_email], subject,
        _wrap_email(
            title="Set Your Password" if is_new_account else "Reset Your Password",
            body_html=body,
            cta_label="Set Password",
            cta_url=set_password_url,
            preheader="Your RAOMS account is ready — set your password to get started.",
        ),
    )
    return ok


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


async def email_bdn_approved(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    bdn_number: str,
    quantity: str,
) -> bool:
    subject = f"BDN Approved — {bdn_number}"
    body = f"""
      <p style="margin:0 0 14px;">Dear {_esc(recipient_name)},</p>
      <p style="margin:0 0 14px;">Bunker Delivery Note <strong>{_esc(bdn_number)}</strong> for
      operation <strong>{_esc(operation_number)}</strong> has been approved.</p>
      <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 14px;">
        <tr>
          <td style="color:{_MUTED};font-size:13px;padding-right:10px;">Quantity Delivered</td>
          <td style="color:{_INK};font-size:13px;font-weight:600;">{_esc(quantity)} MT</td>
        </tr>
      </table>
    """
    return await send_email(
        [to_email], subject,
        _wrap_email(title="Bunker Delivery Note Approved", body_html=body),
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
