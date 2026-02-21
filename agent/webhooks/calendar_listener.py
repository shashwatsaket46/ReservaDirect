from fastapi import APIRouter, Request
from googleapiclient.discovery import build
import pickle
from datetime import datetime

router = APIRouter()

# Global sync token storage
sync_token = None

@router.post("/calendar/webhook")
async def calendar_webhook(request: Request):

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
                calendarId='d91f7dc2fb80684b11bc5f61e7a1d2a14dae6f9ccb2dad75f37610173f9d24a6@group.calendar.google.com',
                singleEvents=True,
                orderBy='startTime'
            ).execute()

        # Next time → only changes
        else:
            events_result = service.events().list(
                calendarId='d91f7dc2fb80684b11bc5f61e7a1d2a14dae6f9ccb2dad75f37610173f9d24a6@group.calendar.google.com',
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

            booking_json = {
                "description": event.get("description"),
                "date": start.strftime("%d/%b/%Y"),
                "time": start.strftime("%H:%M"),
                "duration_minutes": int((end-start).total_seconds()/60)
            }

            print("REAL Booking:", booking_json)

    except Exception as e:
        print("Sync Error:", e)
        sync_token = None

    return {"status": "received"}