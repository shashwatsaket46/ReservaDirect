import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

from fastapi import APIRouter, Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from agent.tools.calendar_booking import create_restaurant_calendar
from agent.webhooks.calendar_watch_api import watch_user_calendar
from agent.db.booking_repo import save_user_google_data
import pickle

router = APIRouter()

SCOPES = ['https://www.googleapis.com/auth/calendar']

REDIRECT_URI = "http://localhost:8000/auth/callback"

CREDENTIALS_PATH = "config/credentials.json"


@router.get("/google/login")
def google_login():

    flow = Flow.from_client_secrets_file(
        CREDENTIALS_PATH,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    auth_url, _ = flow.authorization_url(
        prompt='consent',
        access_type='offline'
    )

    return {"auth_url": auth_url}


@router.get("/auth/callback")
async def google_callback(request: Request):

    flow = Flow.from_client_secrets_file(
        CREDENTIALS_PATH,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )

    flow.fetch_token(
        authorization_response=str(request.url)
    )

    creds = flow.credentials
    calendar_id = create_restaurant_calendar(creds)

    watch_response = watch_user_calendar(creds, calendar_id)

    save_user_google_data({
        "refresh_token": creds.refresh_token,
        "calendar_id": calendar_id,
        "resource_id": watch_response["resourceId"],
        "channel_id": watch_response["id"]
    })

    return {
        "status": "Restaurant Booking Calendar Created",
        "calendar_id": calendar_id
    }