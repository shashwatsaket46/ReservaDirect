"""
ElevenLabs post-call webhook handler.

ElevenLabs fires this endpoint when a voice call to a restaurant ends.
Handles three event types (from ElevenLabs docs):

  post_call_transcription  →  Parse confirmed/actual_time, update Google Calendar
  call_initiation_failure  →  Busy/no-answer — retry with next restaurant
  post_call_audio          →  Ignore (we don't need the audio recording)

Webhook payload structure (from ElevenLabs docs):
{
  "type": "post_call_transcription",
  "event_timestamp": 1739537297,
  "data": {
    "conversation_id": "abc",
    "agent_id": "xyz",
    "status": "done",
    "analysis": {
      "data_collection_results": {
        "confirmed":   {"value": "true"},
        "actual_time": {"value": "7:30 PM"}
      },
      "call_successful": "success"
    }
  }
}

Setup in ElevenLabs dashboard:
  Agents → Settings → Post-call webhook
  → URL: https://your-ngrok-url/webhook/elevenlabs/call-result
  → Copy the HMAC secret → add to .env as ELEVENLABS_WEBHOOK_SECRET
"""

import hashlib
import hmac
import json
import logging
import os
import pickle
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from googleapiclient.discovery import build

from agent.call_state import pending_calls
from agent.config import get_settings

# Path to local reservations log (teammate will import to MongoDB)
_RESERVATIONS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "reservations.json",
)

# New York is UTC-5 (EST) / UTC-4 (EDT) — use fixed -5 for simplicity at a hackathon
_NYC_OFFSET = timezone(timedelta(hours=-5))

logger = logging.getLogger(__name__)
router = APIRouter()

CALENDAR_ID = "d91f7dc2fb80684b11bc5f61e7a1d2a14dae6f9ccb2dad75f37610173f9d24a6@group.calendar.google.com"


@router.post("/webhook/elevenlabs/call-result")
async def call_result_webhook(request: Request):
    """
    Receives post-call events from ElevenLabs when a restaurant call ends.
    Verifies HMAC signature, then routes by event type.
    """
    cfg = get_settings()
    payload_bytes = await request.body()

    # ── HMAC Signature Verification ───────────────────────────────────────────
    # Skip verification if no secret configured (dev/testing only)
    if cfg.elevenlabs_webhook_secret:
        signature = request.headers.get("elevenlabs-signature", "")
        if not _verify_signature(payload_bytes, signature, cfg.elevenlabs_webhook_secret):
            logger.warning("ElevenLabs webhook: invalid signature — rejecting request")
            return JSONResponse({"error": "Invalid signature"}, status_code=401)

    import json
    raw = json.loads(payload_bytes)
    event_type = raw.get("type", "")
    data = raw.get("data", {})
    conversation_id = data.get("conversation_id", "")

    logger.info("ElevenLabs webhook: type=%s conversation_id=%s", event_type, conversation_id)

    if event_type == "post_call_transcription":
        await _handle_transcription(conversation_id, data)

    elif event_type == "call_initiation_failure":
        await _handle_initiation_failure(conversation_id, data)

    elif event_type == "post_call_audio":
        pass  # We don't need the audio recording

    return {"status": "received"}


# ─── Handler: Transcription (call completed) ──────────────────────────────────

async def _handle_transcription(conversation_id: str, data: dict):
    """
    Call completed. Parse whether the booking was confirmed from data_collection_results.
    """
    analysis = data.get("analysis", {})
    collected = analysis.get("data_collection_results", {})

    confirmed = _parse_bool(collected.get("confirmed", {}).get("value", "false"))
    # reservation_time = the time the restaurant agreed to (e.g. "7:30 PM")
    # Checks both "reservation_time" (new name) and "actual_time" (old name) for compatibility
    reservation_time = (
        collected.get("reservation_time", {}).get("value", "")
        or collected.get("actual_time", {}).get("value", "")
    )

    # Fallback: treat call_successful="success" as confirmed if no explicit variable set
    if not confirmed and analysis.get("call_successful") == "success":
        confirmed = True

    # Timestamp of when the reservation was made, in New York time
    reservation_made_at = datetime.now(_NYC_OFFSET).strftime("%Y-%m-%d %H:%M:%S EST")

    logger.info(
        "Transcription result: confirmed=%s reservation_time=%s made_at=%s",
        confirmed, reservation_time, reservation_made_at,
    )

    call_info = pending_calls.pop(conversation_id, None)
    if not call_info:
        logger.warning("No pending call found for conversation_id=%s", conversation_id)
        return

    if confirmed:
        booked_time = reservation_time or call_info.get("time", "")
        _save_reservation({
            "conversation_id": conversation_id,
            "restaurant_name": call_info["restaurant_name"],
            "restaurant_address": call_info["restaurant_address"],
            "user_name": call_info["user_name"],
            "party_size": call_info["party_size"],
            "reservation_date": call_info["date"],
            "reservation_time": booked_time,
            "reservation_made_at": reservation_made_at,
            "calendar_event_id": call_info["calendar_event_id"],
            "status": "confirmed",
        })
        _update_calendar_event(
            event_id=call_info["calendar_event_id"],
            restaurant_name=call_info["restaurant_name"],
            restaurant_address=call_info["restaurant_address"],
            booked_time=booked_time,
        )
    else:
        logger.info("Not confirmed at %s — retrying next restaurant.", call_info["restaurant_name"])
        await _retry_next_restaurant(call_info)


# ─── Handler: Call Initiation Failure ─────────────────────────────────────────

async def _handle_initiation_failure(conversation_id: str, data: dict):
    """
    Call failed to connect (busy, no-answer, etc.).
    Automatically try the next restaurant.

    Failure reasons from ElevenLabs: "busy" | "no-answer" | "unknown"
    """
    failure_reason = data.get("failure_reason", "unknown")
    metadata = data.get("metadata", {})
    provider = metadata.get("type", "")

    logger.info(
        "Call initiation failed: reason=%s provider=%s conversation_id=%s",
        failure_reason, provider, conversation_id,
    )

    call_info = pending_calls.pop(conversation_id, None)
    if not call_info:
        logger.warning("No pending call found for failed call conversation_id=%s", conversation_id)
        return

    # All failure types → try next restaurant
    await _retry_next_restaurant(call_info)


# ─── Google Calendar Update ────────────────────────────────────────────────────

def _update_calendar_event(
    event_id: str,
    restaurant_name: str,
    restaurant_address: str,
    booked_time: str,
):
    """Update the Google Calendar event with confirmed restaurant name + address."""
    token_path = _find_token()
    if not token_path:
        logger.error("token.pickle not found — cannot update calendar event.")
        return

    try:
        with open(token_path, "rb") as f:
            creds = pickle.load(f)

        service = build("calendar", "v3", credentials=creds)

        event = service.events().get(
            calendarId=CALENDAR_ID,
            eventId=event_id,
        ).execute()

        event["summary"] = f"Dinner at {restaurant_name}"
        event["location"] = restaurant_address

        existing_desc = event.get("description", "").strip()
        confirmation_note = (
            f"\n\n✓ Reservation confirmed at {restaurant_name}"
            + (f" for {booked_time}" if booked_time else "")
        )
        event["description"] = existing_desc + confirmation_note

        service.events().update(
            calendarId=CALENDAR_ID,
            eventId=event_id,
            body=event,
        ).execute()

        logger.info(
            "Calendar event %s updated → '%s' at %s",
            event_id, event["summary"], restaurant_address,
        )
    except Exception as exc:
        logger.error("Failed to update calendar event %s: %s", event_id, exc)


# ─── Retry: Next Restaurant ────────────────────────────────────────────────────

async def _retry_next_restaurant(call_info: dict):
    """Search for the next best restaurant and call it."""
    from agent.tools.restaurant_search import search_restaurant
    from agent.tools.booking_voice import make_reservation_call

    next_index = call_info.get("result_index", 0) + 1
    description = call_info.get("event_description", "")
    parsed = _parse_description(description)

    if not parsed:
        logger.warning("Cannot retry — could not parse event description for location/cuisine.")
        return

    try:
        restaurant = await search_restaurant(
            location=parsed["location"],
            cuisine=parsed["cuisine"],
            party_size=call_info["party_size"],
            result_index=next_index,
        )

        if not restaurant.get("name") or restaurant.get("error"):
            logger.warning("No more restaurants at index %d — giving up.", next_index)
            return

        logger.info("Retrying with %s (index=%d)", restaurant["name"], next_index)

        await make_reservation_call(
            restaurant_name=restaurant["name"],
            restaurant_phone=restaurant["phone"],
            user_name=call_info["user_name"],
            party_size=call_info["party_size"],
            date=call_info["date"],
            time=call_info["time"],
            restaurant_address=restaurant.get("address", ""),
            calendar_event_id=call_info["calendar_event_id"],
            result_index=next_index,
            event_description=description,
        )
    except Exception as exc:
        logger.error("Retry failed: %s", exc)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify ElevenLabs HMAC webhook signature without the SDK.
    ElevenLabs sends: elevenlabs-signature: t=<timestamp>,v0=<sha256_hex>
    Signed payload = "<timestamp>.<raw_body>"
    """
    try:
        # Parse t= and v0= from the signature header
        parts = dict(p.split("=", 1) for p in signature.split(",") if "=" in p)
        timestamp = parts.get("t", "")
        received_hmac = parts.get("v0", "")
        if not timestamp or not received_hmac:
            logger.warning("Signature verification failed: missing t= or v0= in header")
            return False

        signed_payload = f"{timestamp}.{payload.decode('utf-8')}".encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, received_hmac)
    except Exception as exc:
        logger.warning("Signature verification failed: %s", exc)
        return False


def _parse_bool(value: str) -> bool:
    return str(value).lower() in ("true", "yes", "1", "confirmed", "success")


def _save_reservation(record: dict):
    """Append a confirmed reservation to reservations.json for MongoDB import."""
    try:
        if os.path.exists(_RESERVATIONS_FILE):
            with open(_RESERVATIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = []
        data.append(record)
        with open(_RESERVATIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("Saved reservation to %s", _RESERVATIONS_FILE)
    except Exception as exc:
        logger.error("Failed to save reservation to JSON: %s", exc)


def _find_token() -> str | None:
    """Find token.pickle — check CWD and project root."""
    for path in ["token.pickle", os.path.join(os.path.dirname(__file__), "..", "..", "token.pickle")]:
        if os.path.exists(path):
            return path
    return None


def _parse_description(description: str) -> dict | None:
    """Parse Location and Cuisine from calendar event description."""
    result = {"location": "", "cuisine": "", "party_size": 2}
    for line in (description or "").splitlines():
        lower = line.lower().strip()
        if lower.startswith("location:"):
            result["location"] = line.split(":", 1)[1].strip()
        elif lower.startswith("cuisine:") or lower.startswith("type:"):
            result["cuisine"] = line.split(":", 1)[1].strip()
        elif any(lower.startswith(k) for k in ["guests:", "people:", "party:"]):
            try:
                result["party_size"] = int(line.split(":", 1)[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
    return result if result["location"] and result["cuisine"] else None
