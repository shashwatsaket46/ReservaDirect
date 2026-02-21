"""
Voice booking tool — Branch B of the autonomous booking engine.
Triggers an ElevenLabs Conversational AI outbound call to the restaurant.
The ElevenLabs voice agent negotiates the reservation autonomously.
"""

import httpx
import logging
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

MAKE_RESERVATION_CALL_SCHEMA = {
    "name": "make_reservation_call",
    "description": (
        "Place an autonomous outbound phone call to a restaurant using ElevenLabs Voice AI. "
        "The AI agent will negotiate a table on behalf of the user. "
        "Use this when digital booking (OpenTable/Resy) is not available. "
        "Returns call_id and status."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "restaurant_name": {"type": "string"},
            "restaurant_phone": {
                "type": "string",
                "description": "E.164 format phone number, e.g. +12125551234",
            },
            "user_name": {"type": "string", "description": "Full name for the reservation"},
            "party_size": {"type": "integer"},
            "date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
            "time": {"type": "string", "description": "Preferred time, e.g. '7:30 PM'"},
            "special_requests": {
                "type": "string",
                "description": "Optional dietary needs or occasion notes",
                "default": "",
            },
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
    special_requests: str = "",
) -> dict[str, Any]:
    cfg = get_settings()

    if cfg.stub_external_apis:
        return {
            "status": "call_initiated",
            "call_id": "STUB-CALL-44291",
            "message": f"[STUB] Voice agent calling {restaurant_name} at {restaurant_phone}",
        }

    api_key = cfg.check_key("elevenlabs_api_key")
    voice_agent_id = cfg.check_key("elevenlabs_voice_agent_id")

    # ElevenLabs Conversational AI — initiate outbound call
    # Docs: https://elevenlabs.io/docs/conversational-ai/phone-calls
    payload = {
        "agent_id": voice_agent_id,
        "agent_phone_number_id": cfg.twilio_phone_number or None,
        "to_number": restaurant_phone,
        "conversation_config_override": {
            "agent": {
                "prompt": {
                    "prompt": (
                        f"You are a polite reservation assistant calling on behalf of {user_name}. "
                        f"You need to book a table for {party_size} people at {restaurant_name} "
                        f"on {date} at {time}. "
                        + (f"Special requests: {special_requests}. " if special_requests else "")
                        + "Be friendly, concise, and confirm the reservation details clearly. "
                        "If the requested time isn't available, ask for the nearest available slot "
                        "and accept if within 1 hour. Always state you are an AI assistant calling "
                        "on behalf of a customer."
                    )
                }
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

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.elevenlabs.io/v1/convai/twilio/outbound-call",
            json=payload,
            headers={"xi-api-key": api_key},
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "status": "call_initiated",
        "call_id": data.get("call_id") or data.get("conversation_id"),
        "message": f"Voice agent is calling {restaurant_name} at {restaurant_phone}. "
                   "I'll update you on WhatsApp once the reservation is confirmed.",
    }
