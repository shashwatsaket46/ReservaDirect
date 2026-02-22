"""
ReservaDirect — FastAPI Application Entry Point

Endpoints:
  GET  /health                         — Health check
  GET  /booking/{session_id}           — Booking status (for Lovable frontend polling)
  POST /webhook/whatsapp               — ElevenLabs WhatsApp incoming messages
  POST /webhook/whatsapp/payment-callback — Payment approval callback
  POST /webhook/stripe                 — Stripe payment events
  POST /dev/simulate                   — Dev-only: simulate a WhatsApp message

Run (from Buildathon/ root):
  uvicorn agent.main:app --reload --port 8001

Expose to internet (for webhooks) via ngrok:
  ngrok http 8000
  Then set the public URL in ElevenLabs and Stripe dashboards.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager

# Allow importing payments/ module (sits one level above agent/).
# append (not insert) so agent/ stays first in sys.path — avoids shadowing agent.py
# with the agent/ package when Python resolves `from agent import get_agent`.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.append(_project_root)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent.config import get_settings
# from agent.webhooks.whatsapp import router as whatsapp_router  # DISABLED: WhatsApp chatbot replaced by Google Calendar flow
# from payments.stripe_webhook import router as stripe_router    # DISABLED: Stripe deposit flow not used in calendar integration
from agent.webhooks.elevenlabs_call import router as elevenlabs_call_router

# ─── Logging ──────────────────────────────────────────────────────────────────

cfg = get_settings()
logging.basicConfig(
    level=getattr(logging, cfg.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ─── App Lifecycle ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ReservaDirect starting up...")
    _print_config_status()
    yield
    logger.info("ReservaDirect shutting down.")


def _print_config_status():
    """Print which integrations are configured at startup."""
    checks = {
        "NVIDIA NIM (Agent + OCR)": bool(cfg.nvidia_api_key),
        "ElevenLabs (WhatsApp/Voice)": bool(cfg.elevenlabs_api_key),
        "Twilio (Phone)": bool(cfg.twilio_account_sid),
        "Google Places (Fallback Search)": bool(cfg.google_places_api_key),
        "Databricks (Primary Search)": bool(cfg.databricks_host and cfg.databricks_token),
        "Nia MCP (Legal Compliance)": bool(cfg.nia_api_key),
        "Supabase (Database)": bool(cfg.supabase_url),
        "Stripe (Payments)": bool(cfg.stripe_secret_key),
        "STUB MODE": cfg.stub_external_apis,
    }
    logger.info("--- Integration Status ---")
    for name, status in checks.items():
        icon = "[OK]" if status else "[--]"
        logger.info("  %s %s", icon, name)
    logger.info("--------------------------")
    if cfg.stub_external_apis:
        logger.warning("STUB_EXTERNAL_APIS=true — all external calls are mocked")


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ReservaDirect API",
    description="Autonomous restaurant reservation agent powered by Claude + ElevenLabs",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Lock this down to your Lovable domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
# app.include_router(whatsapp_router)  # DISABLED: WhatsApp chatbot flow
# app.include_router(stripe_router)    # DISABLED: Stripe deposit flow
app.include_router(elevenlabs_call_router)  # ElevenLabs post-call results


# ─── Core Endpoints ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "ReservaDirect",
        "stub_mode": cfg.stub_external_apis,
    }


@app.get("/booking/{session_id}")
async def get_booking_status(session_id: str):
    """
    Returns current booking status for a session.
    Used by the Lovable frontend to show the live status timeline:
    Searching → Calling Restaurant → Confirming Deposit → Success
    """
    if not cfg.supabase_url or not cfg.supabase_service_key:
        # Return stub status when Supabase isn't configured
        return {
            "session_id": session_id,
            "booking_status": "searching",
            "message": "Supabase not configured — status tracking unavailable",
        }

    try:
        import asyncio
        from supabase import create_client
        client = create_client(cfg.supabase_url, cfg.supabase_service_key)
        result = await asyncio.to_thread(
            lambda: client.table("booking_sessions")
            .select("session_id, booking_status, pending_approval, result_index")
            .eq("session_id", session_id)
            .maybe_single()
            .execute()
        )
        if not result.data:
            raise HTTPException(status_code=404, detail="Session not found")

        data = result.data
        return {
            "session_id": data["session_id"],
            "booking_status": data["booking_status"],
            "awaiting_payment": bool(data.get("pending_approval")),
            "suggestion_number": data.get("result_index", 0) + 1,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch booking status: %s", exc)
        raise HTTPException(status_code=500, detail="Could not fetch booking status")


# ─── Stripe Setup Intent (called by PaymentForm.tsx) ─────────────────────────

class SetupIntentRequest(BaseModel):
    user_id: str        # Supabase auth.uid()
    full_name: str
    phone_number: str
    email: str = ""


@app.post("/api/setup-intent")
async def create_setup_intent(req: SetupIntentRequest):
    """
    Called by PaymentForm.tsx after Supabase signup.
    1. Creates (or retrieves) a Stripe Customer.
    2. Creates a SetupIntent so the frontend can collect card details.
    3. Saves stripe_customer_id back to the profiles table.
    Returns client_secret for Stripe.js to confirm the SetupIntent.
    """
    import asyncio
    import stripe
    stripe.api_key = cfg.check_key("stripe_secret_key")

    # Create Stripe customer
    customer = stripe.Customer.create(
        name=req.full_name,
        email=req.email or None,
        phone=req.phone_number or None,
        metadata={"supabase_uid": req.user_id},
    )

    # Save customer ID to profiles
    if cfg.supabase_url and cfg.supabase_service_key:
        try:
            from supabase import create_client
            sb = create_client(cfg.supabase_url, cfg.supabase_service_key)
            await asyncio.to_thread(
                lambda: sb.table("profiles")
                .update({"stripe_customer_id": customer.id})
                .eq("id", req.user_id)
                .execute()
            )
        except Exception as exc:
            logger.warning("Could not save stripe_customer_id to profiles: %s", exc)

    intent = stripe.SetupIntent.create(
        customer=customer.id,
        payment_method_types=["card"],
        metadata={"supabase_uid": req.user_id},
    )

    return {"client_secret": intent.client_secret, "customer_id": customer.id}


# ─── ElevenLabs Tool Endpoint (Synchronous) ───────────────────────────────────

class MessageRequest(BaseModel):
    from_number: str
    message_text: str
    conversation_id: str = ""


@app.post("/api/message")
async def handle_message(req: MessageRequest):
    """
    Synchronous endpoint called by the ElevenLabs webhook tool.
    ElevenLabs calls this, waits for the reply, then sends it to the user.
    Must respond within response_timeout_secs (20s configured in ElevenLabs).
    """
    from agent.agent import get_agent
    from agent.webhooks.whatsapp import _fetch_user_context

    agent = get_agent()

    try:
        if req.conversation_id:
            reply = await agent.continue_session(req.conversation_id, req.message_text)
            session_id = req.conversation_id
        else:
            session_id, reply = await agent.start_session(
                user_phone=req.from_number,
                message=req.message_text,
                user_context=await _fetch_user_context(req.from_number, cfg),
            )
        return {"reply": reply, "session_id": session_id}
    except Exception as exc:
        logger.error("Error in /api/message: %s", exc)
        return {"reply": "Something went wrong. Please try again in a moment.", "session_id": ""}


# ─── Dev Simulation Endpoint ──────────────────────────────────────────────────

class SimulateRequest(BaseModel):
    phone: str = "+19175550000"
    message: str = "Find me an Italian restaurant in West Village for 2 people tonight at 8pm"
    session_id: str | None = None


@app.post("/dev/simulate")
async def dev_simulate(req: SimulateRequest):
    """
    Simulate an incoming WhatsApp message without needing ElevenLabs.
    Useful for local testing.
    Only available when STUB_EXTERNAL_APIS=true.
    """
    if not cfg.stub_external_apis:
        raise HTTPException(
            status_code=403,
            detail="Dev simulation only available when STUB_EXTERNAL_APIS=true",
        )

    from agent.agent import get_agent
    agent = get_agent()

    if req.session_id:
        reply = await agent.continue_session(req.session_id, req.message)
        return {"reply": reply, "session_id": req.session_id}
    else:
        session_id, reply = await agent.start_session(
            user_phone=req.phone,
            message=req.message,
        )
        return {"reply": reply, "session_id": session_id}

# ─── Dev: Trigger a test voice call ──────────────────────────────────────────

class TestCallRequest(BaseModel):
    restaurant_name: str = "Himalayan Restaurant"
    restaurant_phone: str = "+18624365501"
    user_name: str = "Demo User"
    party_size: int = 11
    date: str = "21/Feb/2026"
    time: str = "7:00 PM"
    restaurant_address: str = "123 Demo St, New York, NY"
    calendar_event_id: str = "demo-event-001"


@app.post("/dev/test-call")
async def dev_test_call(req: TestCallRequest | None = None):
    """
    Directly trigger an ElevenLabs outbound voice call with custom data.
    No STUB_EXTERNAL_APIS restriction — fires a real call.
    """
    if req is None:
        req = TestCallRequest()
    from agent.tools.booking_voice import make_reservation_call
    result = await make_reservation_call(
        restaurant_name=req.restaurant_name,
        restaurant_phone=req.restaurant_phone,
        user_name=req.user_name,
        party_size=req.party_size,
        date=req.date,
        time=req.time,
        restaurant_address=req.restaurant_address,
        calendar_event_id=req.calendar_event_id,
        result_index=0,
        event_description=(
            f"Location: New York\nCuisine: Himalayan\nGuests: {req.party_size}"
        ),
    )
    return result


from agent.webhooks.google_auth import router as google_router
app.include_router(google_router)

from agent.tools.calendar_booking import router as booking_router
app.include_router(booking_router)

from agent.webhooks.calendar_listener import router as calendar_listener_router

app.include_router(calendar_listener_router)

from agent.webhooks.calendar_watch_api import router as calendar_watch_router

app.include_router(calendar_watch_router)