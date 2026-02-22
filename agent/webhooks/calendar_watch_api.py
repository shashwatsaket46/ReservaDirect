from fastapi import APIRouter
from googleapiclient.discovery import build
import pickle
import uuid

router = APIRouter()

def watch_user_calendar(creds, calendar_id):

    service = build("calendar", "v3", credentials=creds)

    request = {
        "id": str(uuid.uuid4()),
        "type": "web_hook",
        "address": "https://pins-believe-know-powerseller.trycloudflare.com/calendar/webhook"
    }

    response = service.events().watch(
        calendarId=calendar_id,
        body=request
    ).execute()

    return response

@router.get("/calendar/start-watch")
def start_watch():

    with open("token.pickle", "rb") as token:
        creds = pickle.load(token)

    service = build("calendar", "v3", credentials=creds)

    request = {
        "id": str(uuid.uuid4()),
        "type": "web_hook",
        "address": "https://pins-believe-know-powerseller.trycloudflare.com/calendar/webhook"
    }

    response = service.events().watch(
        calendarId='d91f7dc2fb80684b11bc5f61e7a1d2a14dae6f9ccb2dad75f37610173f9d24a6@group.calendar.google.com',
        body=request
    ).execute()

    return {
        "status": "Watch Started",
        "channel_id": response.get("id"),
        "resource_id": response.get("resourceId"),
        "expires": response.get("expiration")
    }