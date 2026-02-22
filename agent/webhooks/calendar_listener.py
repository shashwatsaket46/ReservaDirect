from fastapi import APIRouter, Request, BackgroundTasks
from googleapiclient.discovery import build
import pickle
import logging
from datetime import datetime

from agent.tools.restaurant_search import search_restaurant
from agent.tools.booking_voice import make_reservation_call

router = APIRouter()
logger = logging.getLogger(__name__)

# Global sync token storage
sync_token = None

CALENDAR_ID = "d91f7dc2fb80684b11bc5f61e7a1d2a14dae6f9ccb2dad75f37610173f9d24a6@group.calendar.google.com"


@router.post("/calendar/webhook")
async def calendar_webhook(request: Request, background_tasks: BackgroundTasks):

    global sync_token

    headers = request.headers
    resource_state = headers.get("x-goog-resource-state")

    if resource_state != "exists":
        return {"msg": "Not a new event"}

    with open("token.pickle", "rb") as token:
        creds = pickle.load(token)

    service = build("calendar", "v3", credentials=creds)

    try:

        # First time → get full sync
        if sync_token is None:
            events_result = service.events().list(
                calendarId=CALENDAR_ID,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

        # Next time → only changes
        else:
            events_result = service.events().list(
                calendarId=CALENDAR_ID,
                syncToken=sync_token
            ).execute()

        events = events_result.get("items", [])

        # Update sync token for next webhook hit
        sync_token = events_result.get("nextSyncToken")

        for event in events:

            if "start" not in event or "end" not in event:
                continue

            start_raw = event["start"].get("dateTime") or event["start"].get("date")
            end_raw = event["end"].get("dateTime") or event["end"].get("date")

            if not start_raw or not end_raw:
                continue

            if "T" in start_raw:
                start = datetime.fromisoformat(start_raw)
                end = datetime.fromisoformat(end_raw)
            else:
                start = datetime.strptime(start_raw, "%Y-%m-%d")
                end = datetime.strptime(end_raw, "%Y-%m-%d")

            description = event.get("description", "")

            booking_json = {
                "event_id": event.get("id", ""),
                "description": description,
                "date": start.strftime("%d/%b/%Y"),
                "time": start.strftime("%H:%M"),
                "duration_minutes": int((end - start).total_seconds() / 60),
                "guests": _parse_guests(description),
                "location": _parse_field(description, ["location", "area", "neighbourhood"]),
                "cuisine": _parse_field(description, ["cuisine", "type", "food"]),
            }

            print("REAL Booking:", booking_json)
            logger.info("New calendar booking request: %s", booking_json)

            # Trigger reservation pipeline in background — ack Google in < 2s
            background_tasks.add_task(_run_reservation_pipeline, booking_json)

    except Exception as e:
        print("Sync Error:", e)
        sync_token = None

    return {"status": "received"}


# ─── Reservation Pipeline ──────────────────────────────────────────────────────

async def _run_reservation_pipeline(booking: dict):
    """
    1. Search Google Places for the best matching restaurant.
    2. Call it via ElevenLabs voice AI.
    3. ElevenLabs post-call webhook handles the result and updates the calendar.
    """
    location = booking.get("location", "")
    cuisine = booking.get("cuisine", "")
    party_size = booking.get("guests", 2)
    date = booking.get("date", "")
    time = booking.get("time", "")
    calendar_event_id = booking.get("event_id", "")
    description = booking.get("description", "")

    if not location or not cuisine:
        logger.warning(
            "Calendar event missing location or cuisine. "
            "Expected description format:\n"
            "  Location: East Village\n"
            "  Cuisine: Italian\n"
            "  Guests: 4"
        )
        return

    try:
        logger.info(
            "Searching for %s in %s for %d people on %s at %s...",
            cuisine, location, party_size, date, time,
        )

        restaurant = await search_restaurant(
            location=location,
            cuisine=cuisine,
            party_size=party_size,
            result_index=0,
        )

        if not restaurant.get("name") or restaurant.get("error"):
            logger.warning("No restaurant found for %s in %s.", cuisine, location)
            return

        logger.info(
            "Found: %s (difficulty=%s) — calling %s",
            restaurant["name"],
            restaurant.get("difficulty_score"),
            restaurant.get("phone"),
        )

        await make_reservation_call(
            restaurant_name=restaurant["name"],
            restaurant_phone=restaurant["phone"],
            user_name="Guest",
            party_size=party_size,
            date=date,
            time=time,
            restaurant_address=restaurant.get("address", ""),
            calendar_event_id=calendar_event_id,
            result_index=0,
            event_description=description,
        )

    except Exception as exc:
        logger.error("Reservation pipeline error: %s", exc)


# ─── Description Field Parsers ─────────────────────────────────────────────────

def _parse_field(description: str, keys: list[str]) -> str:
    """Extract value from lines like 'Location: East Village'."""
    for line in (description or "").splitlines():
        lower = line.lower().strip()
        for key in keys:
            if lower.startswith(f"{key}:"):
                return line.split(":", 1)[1].strip()
    return ""


def _parse_guests(description: str) -> int:
    """Extract guest count from lines like 'Guests: 4'."""
    for line in (description or "").splitlines():
        lower = line.lower().strip()
        for key in ["guests", "people", "party size", "party"]:
            if lower.startswith(f"{key}:"):
                try:
                    return int(line.split(":", 1)[1].strip().split()[0])
                except (ValueError, IndexError):
                    pass
    return 2
