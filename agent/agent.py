"""
ReservaDirect — Agentic loop powered by NVIDIA Nemotron via NIM.

Uses the OpenAI-compatible NVIDIA NIM API so we reuse the same NVIDIA_API_KEY
already needed for Nemotron OCR (menu scanning). No Anthropic key required.

Model: nvidia/llama-3.1-nemotron-70b-instruct
  - Supports OpenAI-style function/tool calling
  - Available on https://integrate.api.nvidia.com/v1

Implements the full agentic reservation pipeline:
  1. Receive user intent (location, cuisine, time, party size)
  2. Search for best restaurant match
  3. Present result; iterate if rejected
  4. Scan menu image for hidden fees / policies (if URL provided)
  5. Check legal compliance before any voice call
  6. Branch A: digital booking (Resy / OpenTable)
  7. Branch B: voice call (ElevenLabs outbound)
  8. If deposit required → pause loop → request user approval → resume

Session state is persisted in Supabase so the loop can resume after
the user taps "Confirm" on the WhatsApp payment message.
"""

import asyncio
import json
import logging
import uuid
from typing import Any

from openai import AsyncOpenAI

from config import get_settings
from tools import ALL_TOOLS, TOOL_DISPATCH

NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NEMOTRON_MODEL = "meta/llama-3.1-70b-instruct"

logger = logging.getLogger(__name__)

# Loaded from CLAUDE.md at startup
_SYSTEM_PROMPT: str | None = None


def _load_system_prompt() -> str:
    global _SYSTEM_PROMPT
    if _SYSTEM_PROMPT is None:
        try:
            with open("CLAUDE.md", "r", encoding="utf-8") as f:
                _SYSTEM_PROMPT = f.read()
        except FileNotFoundError:
            _SYSTEM_PROMPT = (
                "You are ReservaDirect, an autonomous restaurant reservation agent. "
                "Help users find and book the perfect table as fast as possible. "
                "Always be concise on WhatsApp — one restaurant at a time, max 3 sentences per message."
            )
    return _SYSTEM_PROMPT


class ReservationSession:
    """Holds state for a single user reservation session."""

    def __init__(self, session_id: str, user_phone: str):
        self.session_id = session_id
        self.user_phone = user_phone
        self.messages: list[dict] = []
        self.pending_approval: dict | None = None  # Set when needsApproval=True
        self.result_index: int = 0  # Tracks which restaurant suggestion we're on
        self.booking_status: str = "searching"  # searching → calling → pending_payment → confirmed

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_phone": self.user_phone,
            "messages": self.messages,
            "pending_approval": self.pending_approval,
            "result_index": self.result_index,
            "booking_status": self.booking_status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ReservationSession":
        s = cls(data["session_id"], data["user_phone"])
        s.messages = data["messages"]
        s.pending_approval = data.get("pending_approval")
        s.result_index = data.get("result_index", 0)
        s.booking_status = data.get("booking_status", "searching")
        return s


class ReservationAgent:
    def __init__(self):
        cfg = get_settings()
        # NVIDIA NIM is OpenAI-API-compatible — same key used for Nemotron OCR
        self.client = AsyncOpenAI(
            api_key=cfg.check_key("nvidia_api_key"),
            base_url=NVIDIA_NIM_BASE_URL,
        )
        self.cfg = cfg
        # In-memory session cache — primary store for demo; Supabase is backup
        self._sessions: dict[str, ReservationSession] = {}

    async def start_session(
        self,
        user_phone: str,
        message: str,
        user_context: dict | None = None,
    ) -> tuple[str, str]:
        """
        Start a new reservation session.
        Returns (session_id, agent_reply_text).
        """
        session_id = str(uuid.uuid4())
        session = ReservationSession(session_id=session_id, user_phone=user_phone)

        # Inject user context (name, party size, time from Supabase profile)
        system_addendum = ""
        if user_context:
            system_addendum = (
                f"\n\nUser context: name={user_context.get('name', 'Unknown')}, "
                f"stripe_payment_method_id={user_context.get('stripe_payment_method_id', 'not_set')}, "
                f"preferred_cuisine={user_context.get('preferred_cuisine', '')}"
            )

        session.messages.append({"role": "user", "content": message})

        reply = await self._run_loop(session, system_addendum)
        self._sessions[session_id] = session  # in-memory first
        await self._persist_session(session)
        return session_id, reply

    async def continue_session(
        self,
        session_id: str,
        message: str,
    ) -> str:
        """
        Continue an existing session (user replied on WhatsApp).
        Returns agent reply text.
        """
        # Try in-memory first (fast), fall back to Supabase
        session = self._sessions.get(session_id) or await self._load_session(session_id)
        if not session:
            return "I couldn't find your booking session. Please start a new request."

        # Handle payment approval response
        if session.pending_approval:
            if any(word in message.lower() for word in ["confirm", "yes", "ok", "approve", "charge"]):
                return await self._process_payment_approval(session)
            elif any(word in message.lower() for word in ["no", "cancel", "skip", "don't"]):
                session.pending_approval = None
                session.messages.append({
                    "role": "user",
                    "content": "I declined the deposit. Please suggest a different restaurant.",
                })
                session.result_index += 1
            else:
                return (
                    f"To confirm the ${session.pending_approval['amount_usd']:.2f} deposit, "
                    f"reply *Confirm*. To skip this restaurant, reply *No*."
                )

        session.messages.append({"role": "user", "content": message})
        reply = await self._run_loop(session)
        self._sessions[session_id] = session  # keep in-memory up to date
        await self._persist_session(session)
        return reply

    async def _run_loop(self, session: ReservationSession, system_addendum: str = "") -> str:
        """Core Nemotron agentic loop — OpenAI-compatible message format."""
        system_prompt = _load_system_prompt() + system_addendum

        # Prepend system message (OpenAI format puts it in the messages list)
        messages = [{"role": "system", "content": system_prompt}] + session.messages
        last_text = ""

        search_calls_this_turn = 0  # Prevent the LLM from searching multiple times per turn

        for iteration in range(20):  # Max 20 tool calls per turn
            response = await self.client.chat.completions.create(
                model=NEMOTRON_MODEL,
                max_tokens=4096,
                tools=ALL_TOOLS,
                tool_choice="auto",
                messages=messages,
            )

            choice = response.choices[0]
            msg = choice.message

            last_text = msg.content or ""
            tool_calls = msg.tool_calls or []

            # Append assistant message — API needs the object, session needs a serializable dict
            messages.append(msg)
            msg_dict: dict = {"role": msg.role, "content": msg.content or ""}
            if msg.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ]
            session.messages.append(msg_dict)

            if choice.finish_reason in ("stop", "length") or not tool_calls:
                break

            # Process each tool call
            needs_pause = False
            for tc in tool_calls:
                if tc.function.name == "search_restaurant":
                    search_calls_this_turn += 1

                result = await self._dispatch_tool(tc, session)

                # Append tool result (OpenAI format)
                tool_result_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                }
                messages.append(tool_result_msg)
                session.messages.append(tool_result_msg)

                # Human-in-the-loop pause
                if isinstance(result, dict) and result.get("needs_approval"):
                    session.pending_approval = result
                    session.booking_status = "pending_payment"
                    needs_pause = True

            # After a search, force text-only response — no more tool calls this turn
            if search_calls_this_turn >= 1 and not needs_pause:
                forced = await self.client.chat.completions.create(
                    model=NEMOTRON_MODEL,
                    max_tokens=512,
                    tool_choice="none",
                    messages=messages,
                )
                forced_msg = forced.choices[0].message
                last_text = forced_msg.content or ""
                session.messages.append({"role": forced_msg.role, "content": last_text})
                break

            if needs_pause:
                await self._persist_session(session)
                return last_text or (
                    "This reservation requires a deposit. "
                    "Check your WhatsApp for the payment confirmation message."
                )

        return last_text or "Your reservation request is being processed."

    async def _dispatch_tool(self, tool_call, session: ReservationSession) -> Any:
        """Dispatch an OpenAI tool_call to the correct tool function."""
        tool_name = tool_call.function.name
        try:
            tool_input = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            return {"error": "Invalid tool arguments JSON"}

        logger.info("Dispatching tool: %s | input: %s", tool_name, tool_input)

        # Inject session context for tools that need it
        if tool_name == "request_payment_auth":
            tool_input.setdefault("user_phone", session.user_phone)
            tool_input.setdefault("session_id", session.session_id)

        if tool_name == "search_restaurant":
            tool_input.setdefault("result_index", session.result_index)

        fn = TOOL_DISPATCH.get(tool_name)
        if not fn:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            result = await fn(**tool_input)

            # Track state changes
            if tool_name == "search_restaurant":
                session.booking_status = "searching"
            elif tool_name in ("book_digital", "make_reservation_call"):
                if isinstance(result, dict):
                    if result.get("status") == "confirmed":
                        session.booking_status = "confirmed"
                    elif result.get("status") == "call_initiated":
                        session.booking_status = "calling"

            return result
        except Exception as exc:
            logger.error("Tool %s failed: %s", tool_name, exc)
            return {"error": str(exc)}

    async def _process_payment_approval(self, session: ReservationSession) -> str:
        """User confirmed the deposit — charge the card and resume."""
        from tools.payment_auth import charge_card

        approval = session.pending_approval
        session.pending_approval = None

        try:
            charge_result = await charge_card(
                stripe_payment_method_id=approval.get("stripe_payment_method_id", ""),
                amount_usd=approval["amount_usd"],
                restaurant_name=approval.get("restaurant_name", ""),
            )

            if charge_result.get("status") == "charged":
                session.booking_status = "confirmed"
                session.messages.append({
                    "role": "user",
                    "content": (
                        f"Payment approved and charged: ${approval['amount_usd']:.2f}. "
                        f"Stripe PaymentIntent: {charge_result['payment_intent_id']}. "
                        f"Please confirm the reservation is complete."
                    ),
                })
                await self._persist_session(session)
                reply = await self._run_loop(session)
                await self._persist_session(session)
                return reply
            else:
                return "Payment could not be processed. Please try again or use a different card."

        except Exception as exc:
            logger.error("Payment charge failed: %s", exc)
            return f"Payment failed: {exc}. Please contact support."

    async def _persist_session(self, session: ReservationSession):
        """Save session state to Supabase."""
        if not self.cfg.supabase_url or not self.cfg.supabase_service_key:
            logger.debug("Supabase not configured — session state not persisted.")
            return
        try:
            from supabase import create_client
            client = create_client(self.cfg.supabase_url, self.cfg.supabase_service_key)
            await asyncio.to_thread(
                lambda: client.table("booking_sessions").upsert(session.to_dict()).execute()
            )
        except Exception as exc:
            logger.warning("Failed to persist session: %s", exc)

    async def _load_session(self, session_id: str) -> ReservationSession | None:
        """Load session state from Supabase."""
        if not self.cfg.supabase_url or not self.cfg.supabase_service_key:
            return None
        try:
            from supabase import create_client
            client = create_client(self.cfg.supabase_url, self.cfg.supabase_service_key)
            result = await asyncio.to_thread(
                lambda: client.table("booking_sessions")
                .select("*")
                .eq("session_id", session_id)
                .single()
                .execute()
            )
            if result.data:
                return ReservationSession.from_dict(result.data)
        except Exception as exc:
            logger.warning("Failed to load session %s: %s", session_id, exc)
        return None


# Module-level singleton
_agent: ReservationAgent | None = None


def get_agent() -> ReservationAgent:
    global _agent
    if _agent is None:
        _agent = ReservationAgent()
    return _agent
