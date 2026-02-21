from fastapi import APIRouter, Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
import pickle
import os

router = APIRouter()

SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

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

    with open("token.pickle", "wb") as token:
        pickle.dump(creds, token)

    service = build("calendar", "v3", credentials=creds)

    calendars = service.calendarList().list().execute()

    return {
        "status": "Google Auth Successful",
        "calendars": [c['summary'] for c in calendars['items']]
    }