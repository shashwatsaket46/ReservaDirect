from fastapi import APIRouter
from googleapiclient.discovery import build
import pickle
from datetime import datetime, timedelta

router = APIRouter()

@router.post("/calendar/book")
def book_table(
        description: str,
        date: str,
        time: str,
        duration_minutes: int = 60
):

    with open("token.pickle", "rb") as token:
        creds = pickle.load(token)

    service = build("calendar", "v3", credentials=creds)

    start_datetime = datetime.strptime(
        f"{date} {time}", "%d/%b/%Y %H:%M"
    )

    end_datetime = start_datetime + timedelta(minutes=duration_minutes)

    event = {
        "summary": "Restaurant Booking",
        "description": description,
        "start": {
            "dateTime": start_datetime.isoformat(),
            "timeZone": "Asia/Kolkata"
        },
        "end": {
            "dateTime": end_datetime.isoformat(),
            "timeZone": "Asia/Kolkata"
        }
    }

    created_event = service.events().insert(
        calendarId='d91f7dc2fb80684b11bc5f61e7a1d2a14dae6f9ccb2dad75f37610173f9d24a6@group.calendar.google.com',
        body=event
    ).execute()

    return {
        "status": "Booking Confirmed",
        "event_link": created_event.get("htmlLink")
    }