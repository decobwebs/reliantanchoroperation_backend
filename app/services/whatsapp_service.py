"""
WhatsApp notification service via Twilio.

- Uses Twilio WhatsApp sandbox for development (no Meta approval needed).
- Gracefully degrades — if credentials are missing, logs a warning and returns.
- All sends are fire-and-forget (called via asyncio.create_task); never blocks
  the main request/response cycle.

Setup (one-time per phone number):
  1. Add TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN to .env
  2. User texts "join <sandbox-keyword>" to whatsapp:+14155238886
  3. Add user's phone to their profile in Admin → phone field (e.g. +2348012345678)

Production:
  - Apply for a WhatsApp Business number via Twilio console.
  - Create approved message templates for each event type.
  - Update TWILIO_WHATSAPP_FROM to your approved number.
"""

import asyncio
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger("raoms")

# ── Message templates ─────────────────────────────────────────────────────────
# Keep these under 1600 characters. Emoji improves open rate in field contexts.

TEMPLATES = {
    # Operations
    "task_assigned": (
        "📋 *RAOMS — Task Assigned*\n\n"
        "Hello {name},\n\n"
        "You have been assigned a new task on operation *{operation_number}*.\n\n"
        "Task: *{task_type}*\nPriority: {priority}\n\n"
        "Log in to RAOMS to view details and submit your feedback.\n"
        "_Reliant Anchor Operations_"
    ),
    "operation_created": (
        "🆕 *RAOMS — New Operation*\n\n"
        "Hello {name},\n\n"
        "Operation *{operation_number}* has been created and is ready for task assignment.\n\n"
        "Type: {op_type}\nClient: {client}\n\n"
        "_Reliant Anchor Operations_"
    ),
    "operation_status_changed": (
        "🔄 *RAOMS — Status Update*\n\n"
        "Hello {name},\n\n"
        "Operation *{operation_number}* has moved to:\n"
        "➡️ *{new_status}*\n\n"
        "{extra}\n"
        "_Reliant Anchor Operations_"
    ),
    # Truck / Logistics
    "truck_feedback_requested": (
        "🚛 *RAOMS — Truck Feedback Needed*\n\n"
        "Hello {name},\n\n"
        "Please check truck status for operation *{operation_number}* and submit your feedback.\n\n"
        "_Reliant Anchor Operations_"
    ),
    "truck_feedback_submitted": (
        "🚛 *RAOMS — Truck Feedback Received*\n\n"
        "Hello {name},\n\n"
        "Truck feedback has been submitted for operation *{operation_number}*.\n"
        "Please review and approve or reject.\n\n"
        "_Reliant Anchor Operations_"
    ),
    # Vessel / Marine
    "vessel_task_assigned": (
        "⚓ *RAOMS — Vessel Task Assigned*\n\n"
        "Hello {name},\n\n"
        "A vessel operations task has been assigned for *{operation_number}*.\n\n"
        "Please coordinate with the marine logistics team for vessel discharge/replenishment.\n\n"
        "_Reliant Anchor Operations_"
    ),
    "low_rob_alert": (
        "⚠️ *RAOMS — Low ROB Alert*\n\n"
        "Hello {name},\n\n"
        "Vessel *{vessel_name}* has reached a low Remaining On Board (ROB) level.\n\n"
        "Current ROB: *{current_rob} MT*\nThreshold: {threshold} MT\n\n"
        "Immediate replenishment may be required.\n\n"
        "_Reliant Anchor Operations_"
    ),
    "bdn_submitted": (
        "📄 *RAOMS — BDN Submitted*\n\n"
        "Hello {name},\n\n"
        "A Bunker Delivery Note has been submitted for operation *{operation_number}*.\n\n"
        "BDN: *{bdn_number}*\nQuantity: {quantity} MT\n\n"
        "Please review and approve.\n\n"
        "_Reliant Anchor Operations_"
    ),
    "bdn_approved": (
        "✅ *RAOMS — BDN Approved*\n\n"
        "Hello {name},\n\n"
        "BDN *{bdn_number}* for operation *{operation_number}* has been *approved*.\n\n"
        "The operation is ready to proceed to invoicing.\n\n"
        "_Reliant Anchor Operations_"
    ),
    "bdn_rejected": (
        "❌ *RAOMS — BDN Rejected*\n\n"
        "Hello {name},\n\n"
        "BDN *{bdn_number}* for operation *{operation_number}* has been *rejected*.\n\n"
        "Reason: {reason}\n\n"
        "Please review and resubmit.\n\n"
        "_Reliant Anchor Operations_"
    ),
    # Finance
    "pfi_received": (
        "💰 *RAOMS — PFI Received*\n\n"
        "Hello {name},\n\n"
        "A Pro-Forma Invoice has been linked to operation *{operation_number}*.\n\n"
        "PFI: *{pfi_number}*\nAmount: {amount} {currency}\n\n"
        "Please process payment.\n\n"
        "_Reliant Anchor Operations_"
    ),
    "payment_confirmed": (
        "💳 *RAOMS — Payment Confirmed*\n\n"
        "Hello {name},\n\n"
        "Payment has been confirmed for operation *{operation_number}*.\n\n"
        "Amount: *{amount} {currency}*\nReference: {reference}\n\n"
        "_Reliant Anchor Operations_"
    ),
    "invoice_generated": (
        "🧾 *RAOMS — Invoice Generated*\n\n"
        "Hello {name},\n\n"
        "Invoice *{invoice_number}* has been generated for operation *{operation_number}*.\n\n"
        "Amount: *{amount} {currency}*\n\n"
        "Log in to your portal to view and download.\n\n"
        "_Reliant Anchor Operations_"
    ),
    # Generic fallback
    "generic": (
        "🔔 *RAOMS Notification*\n\n"
        "Hello {name},\n\n"
        "{message}\n\n"
        "_Reliant Anchor Operations_"
    ),
}


def _is_configured() -> bool:
    """Return True only if all required Twilio credentials are present."""
    return bool(
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_AUTH_TOKEN
        and settings.TWILIO_WHATSAPP_FROM
    )


def _format_phone(phone: str) -> str:
    """Ensure phone is in E.164 whatsapp: format."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+"):
        phone = "+" + phone
    if not phone.startswith("whatsapp:"):
        phone = f"whatsapp:{phone}"
    return phone


def _build_message(template_key: str, **kwargs) -> str:
    template = TEMPLATES.get(template_key, TEMPLATES["generic"])
    try:
        return template.format(**kwargs)
    except KeyError:
        # Fall back to generic if template vars don't match
        return TEMPLATES["generic"].format(
            name=kwargs.get("name", "Team"),
            message=kwargs.get("message", "You have a new notification in RAOMS."),
        )


def send_whatsapp(to_phone: str, template_key: str, **kwargs) -> None:
    """
    Synchronous send — called inside asyncio.create_task via _send_async().
    Blocking Twilio HTTP call is intentionally run in a thread pool by the
    async wrapper so it never blocks the event loop.
    """
    if not _is_configured():
        logger.warning(
            "WhatsApp not configured (TWILIO_* vars missing). "
            "Skipping send to %s for event '%s'.",
            to_phone,
            template_key,
        )
        return

    try:
        from twilio.rest import Client
        from twilio.base.exceptions import TwilioRestException

        client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
        body = _build_message(template_key, **kwargs)
        to_formatted = _format_phone(to_phone)

        message = client.messages.create(
            from_=settings.TWILIO_WHATSAPP_FROM,
            to=to_formatted,
            body=body,
        )
        logger.info(
            "WhatsApp sent | sid=%s | to=%s | event=%s",
            message.sid,
            to_formatted,
            template_key,
        )
    except Exception as exc:  # TwilioRestException or network error
        # Never crash the main flow — log and continue
        logger.error(
            "WhatsApp send failed | to=%s | event=%s | error=%s",
            to_phone,
            template_key,
            str(exc),
        )


async def send_whatsapp_async(to_phone: str, template_key: str, **kwargs) -> None:
    """
    Async wrapper — runs the blocking Twilio call in a thread pool executor
    so it never blocks the FastAPI event loop.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: send_whatsapp(to_phone, template_key, **kwargs),
    )


def dispatch(to_phone: Optional[str], template_key: str, **kwargs) -> None:
    """
    Fire-and-forget dispatcher.  Call this from anywhere — it schedules the
    WhatsApp send as a background asyncio task without awaiting.

    Usage:
        from app.services.whatsapp_service import dispatch
        dispatch(user.phone, "task_assigned", name=user.full_name, ...)
    """
    if not to_phone:
        return  # User has no phone number — silently skip

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(send_whatsapp_async(to_phone, template_key, **kwargs))
        else:
            # Fallback for sync contexts (e.g. tests)
            loop.run_until_complete(send_whatsapp_async(to_phone, template_key, **kwargs))
    except Exception as exc:
        logger.error("WhatsApp dispatch error: %s", str(exc))
