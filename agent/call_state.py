"""
Shared in-memory state for pending ElevenLabs voice calls.

When a call is initiated in booking_voice.py, we store the call metadata here.
When ElevenLabs fires the post-call webhook in elevenlabs_call.py, we look it up
to know which Google Calendar event to update.

Structure:
  pending_calls[conversation_id] = {
      "calendar_event_id": str,
      "restaurant_name": str,
      "restaurant_address": str,
      "date": str,
      "time": str,
      "user_name": str,
      "party_size": int,
      "result_index": int,       # which search result this was (for retry)
      "event_description": str,  # original calendar description (for retry)
  }
"""

pending_calls: dict[str, dict] = {}
