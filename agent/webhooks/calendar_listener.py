import asyncio
import logging

from fastapi import APIRouter, Request, BackgroundTasks
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pickle
from datetime import datetime, timezone
from dateutil import parser
from agent.tools.reservation_parser import parse_reservation
from agent.tools.restaurant_search import get_nearby_restaurants
from agent.db.booking_repo import (
    get_sync_token, save_sync_token, clear_sync_token,
    upsert_booking, cancel_booking
)
import json
import threading

from agent.webhooks.elevenlabs_call import make_reservation_call

router = APIRouter()
logger = logging.getLogger(__name__)

CALENDAR_ID = "d91f7dc2fb80684b11bc5f61e7a1d2a14dae6f9ccb2dad75f37610173f9d24a6@group.calendar.google.com"
_sync_lock = threading.Lock()

# ---------------- WEBHOOK ENTRY ----------------

@router.post("/calendar/webhook")
async def calendar_webhook(request: Request, bg: BackgroundTasks):
    print("[WEBHOOK] Received notification")
    headers = request.headers
    resource_state = headers.get("x-goog-resource-state")

    if resource_state == "sync":
        print("Google Handshake Received")
        return {"msg": "Sync acknowledged"}

    if resource_state != "exists":
        return {"msg": "Ignored"}

    bg.add_task(process_calendar_events)
    return {"status": "received"}


# ---------------- BACKGROUND SYNC ----------------
def process_calendar_events():
    if not _sync_lock.acquire(blocking=False):
        print("[SYNC] Already running, skipping duplicate.")
        return
    try:
        print("[SYNC] process_calendar_events started")

        with open("token.pickle", "rb") as token:
            creds = pickle.load(token)

        service = build("calendar", "v3", credentials=creds)
        sync_token = get_sync_token()

        # 1. Fetch Events
        if sync_token is None:
            events_result = service.events().list(
                calendarId=CALENDAR_ID,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
        else:
            events_result = service.events().list(
                calendarId=CALENDAR_ID,
                syncToken=sync_token
            ).execute()

        # 2. IMMEDIATE CHECKPOINT
        # We save the token NOW so that if a new webhook hits while we are
        # doing slow AI work, it won't fetch these same events again.
        new_sync_token = events_result.get("nextSyncToken")
        if new_sync_token:
            save_sync_token(new_sync_token)
            print(f"[SYNC] Checkpoint saved: {new_sync_token}")

        events = events_result.get("items", [])
        print(f"[SYNC] Total events to evaluate: {len(events)}")

        for event in events:
            event_id = event.get("id")
            status = event.get("status")

            # 3. RECENCY FILTER
            # Ignore events that haven't been updated in the last 5 minutes.
            # This prevents historical "sync dumps" from triggering AI calls.
            updated_raw = event.get("updated")
            if updated_raw:
                event_updated_time = parser.isoparse(updated_raw)
                now = datetime.now(timezone.utc)
                diff = (now - event_updated_time).total_seconds()

                if diff > 300: # 5 minutes
                    print(f"[SKIP] Ignoring old event {event_id} (Updated {int(diff)}s ago)")
                    continue

            if status == "cancelled":
                cancel_booking(event_id)
                continue

            if status != "confirmed":
                continue

            if "start" not in event or "end" not in event:
                continue

            # Parsing logic
            start_raw = event["start"].get("dateTime") or event["start"].get("date")
            end_raw = event["end"].get("dateTime") or event["end"].get("date")
            start = parser.isoparse(start_raw)
            end = parser.isoparse(end_raw)

            customer_name = event.get("summary") or event.get("creator", {}).get("email", "Unknown").split("@")[0]
            description = event.get("description", "")

            # AI Parsing (Claude)
            parsed = {"phone_number": "", "number_of_people": 0, "price_range": "Unknown", "special_request": ""}
            if description and len(description.strip()) > 2:
                try:
                    claude_data = parse_reservation(description)
                    parsed.update(claude_data)
                except Exception as e:
                    print(f"Claude Parse Failed: {e}")

            booking_json = {
                "event_id": event_id,
                "guest_name": customer_name,
                "email": event.get("creator", {}).get("email"),
                "phone_number": parsed.get("phone_number"),
                "price_range": parsed.get("price_range"),
                "location": event.get("location"),
                "date": start.strftime("%d/%b/%Y"),
                "time": start.strftime("%H:%M"),
                "duration_minutes": int((end - start).total_seconds() / 60),
                "number_of_people": parsed.get("number_of_people"),
                "special_request": parsed.get("special_request")
            }

            # Restaurant Search (Google Maps)
            location = booking_json.get("location")
            if location:
                try:
                    nearby = get_nearby_restaurants(location)
                    restaurants = nearby.get("restaurants", [])
                    if restaurants:

                        best = restaurants[0]
                        booking_json.update({
                            "restaurant_name": best.get("name"),
                            "restaurant_phone": best.get("phone_number"),
                            "restaurant_address": best.get("address"),
                            "restaurant_rating": best.get("rating")
                        })
                except Exception as e:
                    print(f"Restaurant Search Failed: {e}")

            # 4. ATOMIC UPSERT
            print("Printing booking",booking_json)
            upsert_booking(booking_json)
            print(f"[DONE] Processed event: {event_id}")

            # 5. ELEVENLABS VOICE CALL
            restaurant_phone = booking_json.get("restaurant_phone")
            if restaurant_phone:
                print(f"[CALL] Initiating reservation call for event {event_id} to {restaurant_phone}")
                try:
                    asyncio.run(make_reservation_call(
                        restaurant_name=booking_json.get("restaurant_name", "the restaurant"),
                        restaurant_phone="+18624365501", # HARDCODED FOR TESTING
                        user_name=booking_json.get("guest_name", "Guest"),
                        party_size=booking_json.get("number_of_people") or 2,
                        date=booking_json["date"],
                        time=booking_json["time"],
                        restaurant_address=booking_json.get("restaurant_address", ""),
                        calendar_event_id=event_id,
                        result_index=0,
                        special_requests=booking_json.get("special_request", ""),
                    ))
                    logger.info("[CALL] Initiated reservation call for event %s", event_id)
                except Exception as e:
                    logger.error("[CALL] Failed to initiate reservation call: %s", e)
            else:
                logger.warning("[CALL] No restaurant phone found for event %s — skipping call", event_id)

    except HttpError as e:
        if e.resp.status == 410:
            print("Sync Token Expired → Resetting")
            process_calendar_events()
        else:
            print(f"Sync Error: {e}")
    finally:
        _sync_lock.release()