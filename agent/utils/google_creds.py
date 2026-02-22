from google.oauth2.credentials import Credentials
from agent.config import get_settings

cfg = get_settings()

SCOPES = ['https://www.googleapis.com/auth/calendar']

def build_user_creds(refresh_token):

    creds = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cfg.google_client_id,
        client_secret=cfg.google_client_secret,
        scopes=SCOPES
    )

    return creds