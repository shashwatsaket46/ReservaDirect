"""
Voice booking tool — calls a restaurant via ElevenLabs Conversational AI.

The ElevenLabs voice agent autonomously calls the restaurant, asks for
availability, confirms the reservation, and collects the result.

After the call ends, ElevenLabs fires a post-call webhook to:
  POST /webhook/elevenlabs/call-result

That webhook reads pending_calls to find which Google Calendar event
to update, then updates it with the confirmed restaurant.
"""

import httpx
import logging
from typing import Any

from agent.config import get_settings
from agent.call_state import pending_calls

logger = logging.getLogger(__name__)

MAKE_RESERVATION_CALL_SCHEMA = {
    "name": "make_reservation_call",
    "description": (
        "Initiate an outbound AI voice call to a restaurant to make a reservation. "
        "Always call check_legal_compliance before calling this tool. "
        "Returns immediately with call_id — the result arrives via webhook asynchronously."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "restaurant_name":    {"type": "string", "description": "Name of the restaurant"},
            "restaurant_phone":   {"type": "string", "description": "Phone number to call (E.164 format preferred)"},
            "user_name":          {"type": "string", "description": "Guest name for the reservation"},
            "party_size":         {"type": "integer", "description": "Number of people"},
            "date":               {"type": "string", "description": "Reservation date, e.g. '21/Feb/2026'"},
            "time":               {"type": "string", "description": "Desired reservation time, e.g. '8:00 PM'"},
            "restaurant_address": {"type": "string", "description": "Street address (optional)"},
            "calendar_event_id":  {"type": "string", "description": "Google Calendar event ID to update after confirmation"},
            "result_index":       {"type": "integer", "description": "0-based index of this restaurant suggestion"},
            "event_description":  {"type": "string", "description": "Full calendar event description for retry context"},
            "special_requests":   {"type": "string", "description": "Any special dietary or seating requests"},
        },
        "required": ["restaurant_name", "restaurant_phone", "user_name", "party_size", "date", "time"],
    },
}


async def make_reservation_call(
    restaurant_name: str,
    restaurant_phone: str,
    user_name: str,
    party_size: int,
    date: str,
    time: str,
    restaurant_address: str = "",
    calendar_event_id: str = "",
    result_index: int = 0,
    event_description: str = "",
    special_requests: str = "",
) -> dict[str, Any]:
    """
    Initiate an outbound ElevenLabs voice call to a restaurant.

    Stores call metadata in pending_calls so the post-call webhook can
    map the result back to the correct Google Calendar event.

    Returns immediately with call_id — the actual result arrives
    asynchronously via /webhook/elevenlabs/call-result.
    """
    cfg = get_settings()

    if cfg.stub_external_apis:
        fake_call_id = f"STUB-CALL-{restaurant_name[:6].upper()}"
        pending_calls[fake_call_id] = {
            "calendar_event_id": calendar_event_id,
            "restaurant_name": restaurant_name,
            "restaurant_address": restaurant_address,
            "date": date,
            "time": time,
            "user_name": user_name,
            "party_size": party_size,
            "result_index": result_index,
            "event_description": event_description,
        }
        return {
            "status": "call_initiated",
            "call_id": fake_call_id,
            "message": f"[STUB] Voice agent calling {restaurant_name} at {restaurant_phone}",
        }

    api_key = cfg.check_key("elevenlabs_api_key")
    voice_agent_id = cfg.check_key("elevenlabs_voice_agent_id")

    # Build the call prompt with all booking details injected
    system_prompt = (
        f"You are ReservaDirect, an AI assistant making a restaurant reservation. "
        f"At the very start of the call say: 'Hi, this is an AI assistant calling on behalf "
        f"of {user_name} to make a dinner reservation.' "
        f"Ask if they have a table for {party_size} {'person' if party_size == 1 else 'people'} "
        f"on {date} at {time}. "
        + (f"Special requests: {special_requests}. " if special_requests else "")
        + "If the exact time is unavailable but within 1 hour, accept it and note the actual time. "
        "If they can accommodate us, confirm the reservation under the name "
        f"'{user_name}' and thank them. "
        "If fully unavailable, politely thank them and end the call. "
        "Always be concise — the full call should be under 90 seconds. "
        "Never claim to be a human."
    )

    payload = {
        "agent_id": voice_agent_id,
        "to_number": restaurant_phone,
        "conversation_config_override": {
            "agent": {
                "prompt": {"prompt": system_prompt},
                "first_message": (
                    f"Hi, I'm an AI assistant calling on behalf of {user_name} "
                    f"to make a reservation — a table for "
                    f"{party_size} {'person' if party_size == 1 else 'people'} "
                    f"on {date} at {time}. Do you have availability?"
                ),
            }
        },
        "dynamic_variables": {
            "user_name": user_name,
            "party_size": str(party_size),
            "date": date,
            "time": time,
            "restaurant_name": restaurant_name,
        },
    }

    # agent_phone_number_id = the ElevenLabs Phone Number ID (e.g. "PhNum_xxxx")
    # Set ELEVENLABS_PHONE_NUMBER_ID in .env after importing Twilio number in ElevenLabs dashboard.
    # ElevenLabs → Conversational AI → Phone Numbers → Import Twilio Number → copy the ID shown.
    phone_number_id = getattr(cfg, "elevenlabs_phone_number_id", "") or ""
    if phone_number_id:
        payload["agent_phone_number_id"] = phone_number_id

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.elevenlabs.io/v1/convai/twilio/outbound-call",
            json=payload,
            headers={"xi-api-key": api_key},
        )
        if not resp.is_success:
            logger.error(
                "ElevenLabs outbound-call error %s: %s",
                resp.status_code, resp.text,
            )
        resp.raise_for_status()
        data = resp.json()

    # ElevenLabs webhook always uses conversation_id — use it as the key
    # callSid is the Twilio SID and is NOT the same as ElevenLabs conversation_id
    call_id = data.get("conversation_id") or data.get("call_id") or data.get("callSid", "")

    # Store metadata so the post-call webhook can update the calendar
    if call_id:
        pending_calls[call_id] = {
            "calendar_event_id": calendar_event_id,
            "restaurant_name": restaurant_name,
            "restaurant_address": restaurant_address,
            "date": date,
            "time": time,
            "user_name": user_name,
            "party_size": party_size,
            "result_index": result_index,
            "event_description": event_description,
        }
        logger.info("Call initiated: %s → %s (event=%s)", call_id, restaurant_name, calendar_event_id)

    return {
        "status": "call_initiated",
        "call_id": call_id,
        "message": f"Calling {restaurant_name} at {restaurant_phone} for {party_size} on {date} at {time}.",
    }
