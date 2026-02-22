import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the project root (one level above agent/)
_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # NVIDIA NIM — used for BOTH the agent loop (Nemotron) AND menu OCR
    # Get from: https://build.nvidia.com → any model → Get API Key
    nvidia_api_key: str = ""

    # ElevenLabs
    elevenlabs_api_key: str = ""
    elevenlabs_agent_id: str = ""
    elevenlabs_voice_agent_id: str = ""
    elevenlabs_webhook_secret: str = ""   # From ElevenLabs → Agents → Settings → Post-call webhook
    elevenlabs_phone_number_id: str = ""  # ElevenLabs Phone Number ID (e.g. PhNum_xxxx) — get from Conversational AI → Phone Numbers

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""

    # Google Places
    google_places_api_key: str = ""

    # Databricks
    databricks_host: str = ""
    databricks_token: str = ""
    databricks_restaurant_endpoint: str = "/serving-endpoints/restaurant-search/invocations"

    # Nia MCP
    nia_api_key: str = ""
    nia_mcp_url: str = "https://api.nozomio.com/mcp"

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # App
    port: int = 8000
    log_level: str = "INFO"
    stub_external_apis: bool = False

    def check_key(self, name: str) -> str:
        """Return key value or raise with helpful message pointing to .env.example."""
        value = getattr(self, name, "")
        if not value:
            raise RuntimeError(
                f"Missing required environment variable '{name.upper()}'. "
                f"See .env.example for setup instructions."
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
