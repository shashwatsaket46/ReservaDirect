import logging

from fastapi import APIRouter, Request, BackgroundTasks
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pickle
from datetime import datetime

from agent.tools.restaurant_search import search_restaurant
from agent.tools.booking_voice import make_reservation_call

router = APIRouter()
logger = logging.getLogger(__name__)

CALENDAR_ID = "d91f7dc2fb80684b11bc5f61e7a1d2a14dae6f9ccb2dad75f37610173f9d24a6@group.calendar.google.com"

# ---------------- GLOBAL MEMORY ----------------
sync_token = None
processed_event_ids: set = set()

@router.post("/calendar/webhook")
async def calendar_webhook(request: Request):

    global sync_token

    headers = request.headers
    resource_state = headers.get("x-goog-resource-state")

    # Google handshake event
    if resource_state == "sync":
        print("Google Handshake Received")
        return {"msg": "Sync acknowledged"}

    # Ignore deletes / updates
    if resource_state != "exists":
        return {"msg": "Ignored"}

    # Run heavy sync in background
    bg.add_task(process_calendar_events)

    return {"status": "received"}


# ---------------- BACKGROUND SYNC ----------------

def process_calendar_events():

    global sync_token
    global processed_event_ids

    with open("token.pickle", "rb") as token:
        creds = pickle.load(token)

    service = build("calendar", "v3", credentials=creds)

    try:

        # Initial sync OR delta sync
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

        events = events_result.get("items", [])
        sync_token = events_result.get("nextSyncToken")

        for event in events:

            event_id = event.get("id")

            # Avoid duplicate processing
            if event_id in processed_event_ids:
                continue

            processed_event_ids.add(event_id)

            # Ignore cancelled
            if event.get("status") != "confirmed":
                continue

            if "start" not in event or "end" not in event:
                continue

            start_raw = event["start"].get("dateTime") or event["start"].get("date")
            end_raw = event["end"].get("dateTime") or event["end"].get("date")

            if not start_raw or not end_raw:
                continue

            start = parser.isoparse(start_raw)
            end = parser.isoparse(end_raw)

            # -------- GOOGLE NAME FALLBACK --------

            creator = event.get("creator", {})
            organizer = event.get("organizer", {})
            attendees = event.get("attendees", [])

            google_creator_name = creator.get("displayName")
            organizer_name = organizer.get("displayName")

            attendee_name = None
            if attendees:
                attendee_name = attendees[0].get("displayName")

            fallback_name = (
                    google_creator_name
                    or attendee_name
                    or organizer_name
                    or event.get("summary")
                    or "Unknown"
            )

            # -------- DESCRIPTION NLP --------

            description = event.get("description", "")

            parsed = {
                "guest_name": fallback_name,
                "phone_number": "",
                "number_of_people": 0,
                "special_request": ""
            }

            if description and len(description.strip()) > 10:
                try:
                    claude_data = parse_reservation(description)
                    parsed["guest_name"] = claude_data.get("guest_name") or fallback_name
                    parsed["phone_number"] = claude_data.get("phone_number")
                    parsed["number_of_people"] = claude_data.get("number_of_people")
                    parsed["special_request"] = claude_data.get("special_request")
                except Exception as e:
                    print("Claude Parse Failed:", e)

            # -------- FINAL BOOKING --------

            description = event.get("description", "")

            booking_json = {
                "description": event.get("description"),
                "date": start.strftime("%d/%b/%Y"),
                "time": start.strftime("%H:%M"),
                "duration_minutes": int((end-start).total_seconds()/60)
            }

            print("REAL Booking:", booking_json)
            logger.info("New calendar booking request: %s", booking_json)

            # Trigger reservation pipeline in background — ack Google in < 2s
            background_tasks.add_task(_run_reservation_pipeline, booking_json)

    except Exception as e:
        print("Sync Error:", e)
        sync_token = None

    return {"status": "received"}