from fastapi import APIRouter, Request, BackgroundTasks
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pickle
from dateutil import parser
from agent.tools.reservation_parser import parse_reservation
from agent.tools.restaurant_search import get_nearby_restaurants

router = APIRouter()

# ---------------- GLOBAL MEMORY ----------------
sync_token = None
processed_event_ids = set()

CALENDAR_ID = "d91f7dc2fb80684b11bc5f61e7a1d2a14dae6f9ccb2dad75f37610173f9d24a6@group.calendar.google.com"

# ---------------- WEBHOOK ENTRY ----------------

@router.post("/calendar/webhook")
async def calendar_webhook(request: Request, bg: BackgroundTasks):

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

            booking_json = {
                "guest_name": parsed["guest_name"],
                "phone_number": parsed["phone_number"],
                "location": event.get("location"),
                "date": start.strftime("%d/%b/%Y"),
                "time": start.strftime("%H:%M"),
                "duration_minutes": int((end - start).total_seconds() / 60),
                "number_of_people": parsed["number_of_people"],
                "special_request": parsed["special_request"]
            }

            # -------- RESTAURANT SEARCH --------

            location = booking_json.get("location")

            if location:
                try:
                    nearby = get_nearby_restaurants(location)
                    booking_json["nearby_restaurants"] = nearby.get("restaurants", [])
                except Exception as e:
                    print("Restaurant Fetch Failed:", e)
                    booking_json["nearby_restaurants"] = []
            else:
                booking_json["nearby_restaurants"] = []

            print("REAL Booking:", booking_json)

    except HttpError as e:
        if e.resp.status == 410:
            print("Sync Token Expired → Resetting")
            sync_token = None
        else:
            print("Sync Error:", e)