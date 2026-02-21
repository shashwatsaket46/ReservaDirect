"""
WhatsApp webhook handler — receives messages from ElevenLabs Conversational AI.

ElevenLabs posts to this endpoint when a user sends a WhatsApp message to
the agent's business number. We:
  1. Ack immediately (200) — ElevenLabs requires sub-2s response.
  2. Parse intent in a background task.
  3. Run the Claude agent loop.
  4. Send the reply back via ElevenLabs send-message API.

Payload reference:
  https://elevenlabs.io/docs/conversational-ai/whatsapp
"""

import asyncio
import logging
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from agent import get_agent
from config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── ElevenLabs Incoming Payload Models ──────────────────────────────────────

class WhatsAppMessage(BaseModel):
    """Minimal model for ElevenLabs WhatsApp webhook payload."""
    from_number: str = ""           # User's phone in E.164
    message_text: str = ""          # Text body
    media_url: str | None = None    # Image URL (for menu scans)
    conversation_id: str = ""       # ElevenLabs conversation/session ID
    agent_id: str = ""
    metadata: dict = {}


class ElevenLabsWebhookPayload(BaseModel):
    """Top-level ElevenLabs webhook envelope."""
    event_type: str = "message"     # message | call_started | call_ended
    data: dict = {}


# ─── Webhook Endpoint ─────────────────────────────────────────────────────────

@router.post("/webhook/whatsapp")
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Receive WhatsApp messages from ElevenLabs.
    Ack immediately, process in background.
    """
    try:
        raw = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info("WhatsApp webhook received: %s", raw)

    event_type = raw.get("event_type", "message")

    if event_type not in ("message", "user_message"):
        # Ignore non-message events (call_started, etc.)
        return {"status": "ignored", "event_type": event_type}

    # Parse the message
    msg = _parse_payload(raw)
    if not msg.from_number or not msg.message_text:
        return {"status": "ignored", "reason": "empty message"}

    # Schedule background processing — return 200 immediately
    background_tasks.add_task(_handle_message, msg)
    return {"status": "accepted"}


@router.post("/webhook/whatsapp/payment-callback")
async def payment_callback(request: Request, background_tasks: BackgroundTasks):
    """
    Called when user taps Confirm/Cancel on the WhatsApp payment message.
    Resumes the paused agent session.
    """
    raw = await request.json()
    session_id = raw.get("session_id") or raw.get("metadata", {}).get("session_id")
    user_reply = raw.get("reply") or raw.get("message_text", "")
    user_phone = raw.get("from_number", "")

    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")

    background_tasks.add_task(_resume_session, session_id, user_reply, user_phone)
    return {"status": "accepted"}


# ─── Background Task Handlers ─────────────────────────────────────────────────

async def _handle_message(msg: WhatsAppMessage):
    """Process an incoming WhatsApp message through the agent."""
    cfg = get_settings()
    agent = get_agent()

    try:
        # Check if this is a continuation of an existing session
        # (ElevenLabs passes the conversation_id for ongoing chats)
        session_id = msg.conversation_id or msg.metadata.get("session_id")

        if session_id:
            reply = await agent.continue_session(session_id, msg.message_text)
        else:
            # New session — enrich message with media URL if present
            user_message = msg.message_text
            if msg.media_url:
                user_message += f"\n[Menu image attached: {msg.media_url}]"

            session_id, reply = await agent.start_session(
                user_phone=msg.from_number,
                message=user_message,
                user_context=await _fetch_user_context(msg.from_number, cfg),
            )

        await _send_whatsapp_reply(
            cfg=cfg,
            to=msg.from_number,
            text=reply,
            session_id=session_id,
            agent_id=msg.agent_id,
        )

    except Exception as exc:
        logger.error("Error handling WhatsApp message from %s: %s", msg.from_number, exc)
        await _send_whatsapp_reply(
            cfg=cfg,
            to=msg.from_number,
            text="Sorry, something went wrong. Please try again in a moment.",
            agent_id=msg.agent_id,
        )


async def _resume_session(session_id: str, user_reply: str, user_phone: str):
    """Resume a paused agent session (after payment approval)."""
    cfg = get_settings()
    agent = get_agent()

    try:
        reply = await agent.continue_session(session_id, user_reply)
        await _send_whatsapp_reply(
            cfg=cfg,
            to=user_phone,
            text=reply,
            session_id=session_id,
        )
    except Exception as exc:
        logger.error("Error resuming session %s: %s", session_id, exc)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_payload(raw: dict) -> WhatsAppMessage:
    """
    Normalize ElevenLabs webhook payload variations.
    ElevenLabs may send the data at the top level or nested in 'data'.
    """
    data = raw.get("data", raw)
    return WhatsAppMessage(
        from_number=data.get("from_number") or data.get("from") or raw.get("from_number", ""),
        message_text=(
            data.get("message_text")
            or data.get("text")
            or data.get("body")
            or raw.get("message_text", "")
        ),
        media_url=data.get("media_url") or raw.get("media_url"),
        conversation_id=(
            data.get("conversation_id")
            or data.get("session_id")
            or raw.get("conversation_id", "")
        ),
        agent_id=data.get("agent_id") or raw.get("agent_id", ""),
        metadata=data.get("metadata") or raw.get("metadata") or {},
    )


async def _fetch_user_context(phone: str, cfg) -> dict[str, Any]:
    """Look up user profile from Supabase profiles table (matches frontend schema)."""
    if not cfg.supabase_url or not cfg.supabase_service_key:
        return {}
    try:
        from supabase import create_client
        client = create_client(cfg.supabase_url, cfg.supabase_service_key)
        result = await asyncio.to_thread(
            lambda: client.table("profiles")
            .select("full_name, stripe_payment_method_id, preferred_cuisines")
            .eq("phone_number", phone)
            .limit(1)
            .execute()
        )
        if not result.data:
            return {}
        d = result.data
        # Normalise column names to what the agent expects
        return {
            "name": d.get("full_name", ""),
            "stripe_payment_method_id": d.get("stripe_payment_method_id", ""),
            "preferred_cuisine": (d.get("preferred_cuisines") or [""])[0],
        }
    except Exception as exc:
        logger.warning("Could not fetch user context for %s: %s", phone, exc)
        return {}


async def _send_whatsapp_reply(
    cfg,
    to: str,
    text: str,
    session_id: str = "",
    agent_id: str = "",
):
    """Send a WhatsApp message back to the user via ElevenLabs."""
    if not cfg.elevenlabs_api_key:
        logger.warning("ELEVENLABS_API_KEY not set — reply not sent: %s", text)
        return

    target_agent_id = agent_id or cfg.elevenlabs_agent_id
    if not target_agent_id:
        logger.warning("ELEVENLABS_AGENT_ID not set — reply not sent.")
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/convai/agents/{target_agent_id}/send-message",
                json={
                    "to": to,
                    "message": text,
                    "metadata": {"session_id": session_id} if session_id else {},
                },
                headers={"xi-api-key": cfg.elevenlabs_api_key},
            )
            if resp.status_code not in (200, 201):
                logger.warning(
                    "WhatsApp reply send failed: %s %s", resp.status_code, resp.text[:200]
                )
    except Exception as exc:
        logger.error("Failed to send WhatsApp reply to %s: %s", to, exc)
