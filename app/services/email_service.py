"""
Email service via Resend API.
Gracefully degrades (logs warning) when RESEND_API_KEY is not configured.
"""
import logging
from typing import List, Optional

import httpx

from app.config import settings

logger = logging.getLogger("raoms.email")

RESEND_API_URL = "https://api.resend.com/emails"


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

async def email_task_assigned(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    task_type: str,
) -> bool:
    subject = f"Task Assigned — Operation {operation_number}"
    html = f"""
    <p>Hello {recipient_name},</p>
    <p>You have been assigned a <strong>{task_type.replace('_', ' ').title()}</strong>
    task on operation <strong>{operation_number}</strong>.</p>
    <p>Please log in to the RAOMS portal to review and accept your assignment.</p>
    <p>— Reliant Anchor Operations Team</p>
    """
    return await send_email([to_email], subject, html)


async def email_pfi_linked(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    pfi_number: str,
    amount: str,
    currency: str,
) -> bool:
    subject = f"PFI Ready for Payment — {pfi_number}"
    html = f"""
    <p>Hello {recipient_name},</p>
    <p>A Pro-Forma Invoice has been linked to operation <strong>{operation_number}</strong>:</p>
    <ul>
      <li><strong>PFI Number:</strong> {pfi_number}</li>
      <li><strong>Amount:</strong> {currency} {amount}</li>
    </ul>
    <p>Please process payment at your earliest convenience.</p>
    <p>— Reliant Anchor Operations Team</p>
    """
    return await send_email([to_email], subject, html)


async def email_payment_confirmed(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    voucher_number: str,
) -> bool:
    subject = f"Payment Confirmed — Operation {operation_number}"
    html = f"""
    <p>Hello {recipient_name},</p>
    <p>Payment voucher <strong>{voucher_number}</strong> for operation
    <strong>{operation_number}</strong> has been confirmed.</p>
    <p>The operation is now cleared to proceed to vessel operations.</p>
    <p>— Reliant Anchor Operations Team</p>
    """
    return await send_email([to_email], subject, html)


async def email_bdn_approved(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    bdn_number: str,
    quantity: str,
) -> bool:
    subject = f"BDN Approved — {bdn_number}"
    html = f"""
    <p>Hello {recipient_name},</p>
    <p>Bunker Delivery Note <strong>{bdn_number}</strong> for operation
    <strong>{operation_number}</strong> has been approved.</p>
    <ul>
      <li><strong>Quantity Delivered:</strong> {quantity} MT</li>
    </ul>
    <p>— Reliant Anchor Operations Team</p>
    """
    return await send_email([to_email], subject, html)


async def email_feedback_rejected(
    to_email: str,
    recipient_name: str,
    operation_number: str,
    reason: str,
) -> bool:
    subject = f"Truck Feedback Rejected — Operation {operation_number}"
    html = f"""
    <p>Hello {recipient_name},</p>
    <p>Your truck readiness feedback for operation <strong>{operation_number}</strong>
    has been rejected.</p>
    <p><strong>Reason:</strong> {reason}</p>
    <p>Please address the issue and resubmit.</p>
    <p>— Reliant Anchor Operations Team</p>
    """
    return await send_email([to_email], subject, html)
