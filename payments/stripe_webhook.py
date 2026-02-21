"""
Stripe webhook handler.

Listens for Stripe events and updates booking status in Supabase.
Mount this router in agent/main.py alongside the WhatsApp router.

Events handled:
  - payment_intent.succeeded → mark booking as paid, send WhatsApp confirmation
  - payment_intent.payment_failed → notify user via WhatsApp
"""

import asyncio
import logging

import stripe
from fastapi import APIRouter, Header, HTTPException, Request

from agent.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/webhook/stripe")
async def stripe_webhook(
    request: Request,
    stripe_signature: str = Header(default="", alias="stripe-signature"),
):
    """
    Verify and process Stripe webhook events.
    Stripe signs every webhook — we verify with the webhook secret.
    """
    cfg = get_settings()
    raw_body = await request.body()

    if not cfg.stripe_webhook_secret:
        logger.warning("STRIPE_WEBHOOK_SECRET not set — skipping signature verification.")
        event_data = await request.json()
        event = stripe.Event.construct_from(event_data, stripe.api_key)
    else:
        stripe.api_key = cfg.stripe_secret_key
        try:
            event = stripe.Webhook.construct_event(
                raw_body, stripe_signature, cfg.stripe_webhook_secret
            )
        except stripe.error.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid Stripe signature")

    logger.info("Stripe event: %s | id: %s", event["type"], event["id"])

    match event["type"]:
        case "payment_intent.succeeded":
            await _handle_payment_succeeded(event["data"]["object"], cfg)
        case "payment_intent.payment_failed":
            await _handle_payment_failed(event["data"]["object"], cfg)
        case "payment_method.attached":
            # User added a card via the Lovable frontend
            await _handle_payment_method_attached(event["data"]["object"], cfg)
        case _:
            logger.debug("Unhandled Stripe event type: %s", event["type"])

    return {"received": True}


async def _handle_payment_succeeded(payment_intent: dict, cfg):
    """Update Supabase booking status and notify user via WhatsApp."""
    pi_id = payment_intent.get("id")
    metadata = payment_intent.get("metadata", {})
    restaurant = metadata.get("restaurant", "the restaurant")

    logger.info("Payment succeeded: %s for %s", pi_id, restaurant)

    # Update booking in Supabase
    await _update_supabase_booking(
        cfg=cfg,
        payment_intent_id=pi_id,
        status="paid",
        metadata=metadata,
    )

    # Look up user phone from Supabase customer record
    customer_id = payment_intent.get("customer")
    user_phone = await _get_user_phone(cfg, customer_id)

    if user_phone and cfg.elevenlabs_api_key and cfg.elevenlabs_agent_id:
        await _send_whatsapp(
            cfg=cfg,
            to=user_phone,
            text=(
                f"Payment confirmed! Your deposit of ${payment_intent['amount'] / 100:.2f} "
                f"for *{restaurant}* has been charged. Your reservation is confirmed. Enjoy!"
            ),
        )


async def _handle_payment_failed(payment_intent: dict, cfg):
    """Notify user that payment failed."""
    metadata = payment_intent.get("metadata", {})
    restaurant = metadata.get("restaurant", "the restaurant")
    customer_id = payment_intent.get("customer")

    logger.warning("Payment failed: %s for %s", payment_intent.get("id"), restaurant)

    user_phone = await _get_user_phone(cfg, customer_id)
    if user_phone and cfg.elevenlabs_api_key and cfg.elevenlabs_agent_id:
        await _send_whatsapp(
            cfg=cfg,
            to=user_phone,
            text=(
                f"Your payment for *{restaurant}* failed. "
                f"Please update your card details and try again, "
                f"or reply 'skip' to choose a different restaurant."
            ),
        )


async def _handle_payment_method_attached(payment_method: dict, cfg):
    """Store the PaymentMethod ID in the user's Supabase profile."""
    customer_id = payment_method.get("customer")
    pm_id = payment_method.get("id")

    if not customer_id or not pm_id:
        return

    await asyncio.to_thread(lambda: _sync_pm_to_supabase(cfg, customer_id, pm_id))


def _sync_pm_to_supabase(cfg, customer_id: str, pm_id: str):
    if not cfg.supabase_url or not cfg.supabase_service_key:
        return
    try:
        from supabase import create_client
        client = create_client(cfg.supabase_url, cfg.supabase_service_key)
        client.table("profiles").update({"stripe_payment_method_id": pm_id}).eq(
            "stripe_customer_id", customer_id
        ).execute()
    except Exception as exc:
        logger.warning("Failed to sync PaymentMethod to Supabase: %s", exc)


async def _update_supabase_booking(cfg, payment_intent_id: str, status: str, metadata: dict):
    if not cfg.supabase_url or not cfg.supabase_service_key:
        return
    try:
        from supabase import create_client
        client = create_client(cfg.supabase_url, cfg.supabase_service_key)
        await asyncio.to_thread(
            lambda: client.table("booking_sessions")
            .update({"booking_status": status, "payment_intent_id": payment_intent_id})
            .eq("session_id", metadata.get("session_id", ""))
            .execute()
        )
    except Exception as exc:
        logger.warning("Failed to update booking status: %s", exc)


async def _get_user_phone(cfg, customer_id: str | None) -> str | None:
    if not customer_id or not cfg.supabase_url or not cfg.supabase_service_key:
        return None
    try:
        from supabase import create_client
        client = create_client(cfg.supabase_url, cfg.supabase_service_key)
        result = await asyncio.to_thread(
            lambda: client.table("profiles")
            .select("phone_number")
            .eq("stripe_customer_id", customer_id)
            .maybe_single()
            .execute()
        )
        return result.data.get("phone_number") if result.data else None
    except Exception:
        return None


async def _send_whatsapp(cfg, to: str, text: str):
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"https://api.elevenlabs.io/v1/convai/agents/{cfg.elevenlabs_agent_id}/send-message",
                json={"to": to, "message": text},
                headers={"xi-api-key": cfg.elevenlabs_api_key},
            )
    except Exception as exc:
        logger.warning("Failed to send WhatsApp notification: %s", exc)
