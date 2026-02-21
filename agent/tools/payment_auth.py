"""
Payment authorization tool — Human-in-the-loop pattern.
When a restaurant requires a deposit, this tool:
  1. Sends a WhatsApp message asking the user to confirm the charge.
  2. Returns needs_approval=True to pause the agent loop.
  3. The agent resumes when the user taps Confirm (handled by the webhook).
"""

import httpx
import logging
import stripe
from typing import Any

from agent.config import get_settings

logger = logging.getLogger(__name__)

REQUEST_PAYMENT_AUTH_SCHEMA = {
    "name": "request_payment_auth",
    "description": (
        "Request user approval for a payment or deposit required by the restaurant. "
        "Sends a WhatsApp message to the user with the amount and a Confirm button. "
        "The agent will pause and wait for the user to approve before continuing. "
        "Use this whenever a restaurant requires upfront payment or a cancellation hold."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "amount_usd": {
                "type": "number",
                "description": "Deposit or hold amount in USD, e.g. 25.00",
            },
            "reason": {
                "type": "string",
                "description": "Why payment is required, e.g. 'cancellation hold' or 'prepaid tasting menu'",
            },
            "restaurant_name": {"type": "string"},
            "user_phone": {
                "type": "string",
                "description": "User's WhatsApp phone in E.164 format",
            },
            "stripe_payment_method_id": {
                "type": "string",
                "description": "Stripe PaymentMethod ID stored from user onboarding (pm_...)",
            },
            "session_id": {
                "type": "string",
                "description": "Current booking session ID for resuming the agent after approval",
            },
        },
        "required": ["amount_usd", "reason", "restaurant_name", "user_phone", "session_id"],
    },
}


async def request_payment_auth(
    amount_usd: float,
    reason: str,
    restaurant_name: str,
    user_phone: str,
    session_id: str,
    stripe_payment_method_id: str = "",
) -> dict[str, Any]:
    cfg = get_settings()

    if cfg.stub_external_apis:
        logger.info("[STUB] Payment auth requested: $%.2f for %s", amount_usd, restaurant_name)
        return {
            "needs_approval": True,
            "status": "pending_user_approval",
            "message": (
                f"[STUB] WhatsApp sent: '{restaurant_name} requires a ${amount_usd:.2f} {reason}. "
                f"Tap Confirm to charge your card on file.'"
            ),
            "session_id": session_id,
        }

    # Send WhatsApp message via ElevenLabs
    await _send_whatsapp_approval_request(
        cfg=cfg,
        user_phone=user_phone,
        restaurant_name=restaurant_name,
        amount_usd=amount_usd,
        reason=reason,
        session_id=session_id,
    )

    return {
        "needs_approval": True,
        "status": "pending_user_approval",
        "message": (
            f"Sent payment approval request to user. "
            f"Waiting for confirmation of ${amount_usd:.2f} {reason} for {restaurant_name}."
        ),
        "session_id": session_id,
        "amount_usd": amount_usd,
        "stripe_payment_method_id": stripe_payment_method_id,
    }


async def charge_card(
    stripe_payment_method_id: str,
    amount_usd: float,
    restaurant_name: str,
    customer_id: str = "",
) -> dict[str, Any]:
    """
    Called after user approves the payment via WhatsApp.
    Creates a Stripe PaymentIntent and confirms it immediately.
    """
    cfg = get_settings()
    stripe.api_key = cfg.check_key("stripe_secret_key")

    amount_cents = int(amount_usd * 100)

    intent = stripe.PaymentIntent.create(
        amount=amount_cents,
        currency="usd",
        payment_method=stripe_payment_method_id,
        customer=customer_id or None,
        confirm=True,
        automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
        description=f"ReservaDirect deposit — {restaurant_name}",
        metadata={"restaurant": restaurant_name, "platform": "reservadirect"},
    )

    return {
        "status": "charged",
        "payment_intent_id": intent.id,
        "amount_usd": amount_usd,
        "stripe_status": intent.status,
    }


async def _send_whatsapp_approval_request(
    cfg,
    user_phone: str,
    restaurant_name: str,
    amount_usd: float,
    reason: str,
    session_id: str,
):
    """
    Send a WhatsApp interactive message with a Confirm button.
    ElevenLabs WhatsApp API passes through to the WhatsApp Business Platform.
    """
    api_key = cfg.check_key("elevenlabs_api_key")
    agent_id = cfg.check_key("elevenlabs_agent_id")

    message = (
        f"*ReservaDirect* \n\n"
        f"Your reservation at *{restaurant_name}* requires a "
        f"*${amount_usd:.2f} {reason}*.\n\n"
        f"Tap *Confirm* to charge your card on file, or *Cancel* to skip this restaurant."
    )

    # ElevenLabs send message endpoint
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"https://api.elevenlabs.io/v1/convai/agents/{agent_id}/send-message",
            json={
                "to": user_phone,
                "message": message,
                "metadata": {
                    "session_id": session_id,
                    "action": "payment_approval",
                    "amount_usd": amount_usd,
                },
            },
            headers={"xi-api-key": api_key},
        )
        if resp.status_code not in (200, 201):
            logger.warning("WhatsApp send failed: %s %s", resp.status_code, resp.text)
