"""
Legal compliance tool — Nia MCP Server (Nozomio).
Queries indexed knowledge of NY Senate Bill S9365A (Restaurant Reservation
Anti-Piracy Act) to ensure automated calls comply with local law.

Key rules checked:
  - Agent must identify itself as AI
  - Cannot resell or broker reservations
  - Must act on behalf of a named human customer
  - Cannot impersonate restaurant staff or claim to be a human caller
"""

import httpx
import logging
from typing import Any

from agent.config import get_settings

logger = logging.getLogger(__name__)

CHECK_LEGAL_COMPLIANCE_SCHEMA = {
    "name": "check_legal_compliance",
    "description": (
        "Check that the planned reservation call complies with the NY Restaurant "
        "Reservation Anti-Piracy Act (S9365A) and other applicable laws. "
        "Query the Nia MCP knowledge base for relevant rules. "
        "Call this before initiating any voice call to a restaurant. "
        "Returns approved=True with guidelines, or approved=False with the violation reason."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action_description": {
                "type": "string",
                "description": (
                    "Plain-English description of what the agent intends to do, "
                    "e.g. 'Call Carbone on behalf of John Smith to book a table for 2 on Friday at 7pm'"
                ),
            },
            "is_for_resale": {
                "type": "boolean",
                "description": "True if this reservation will be resold or brokered (always False for ReservaDirect)",
                "default": False,
            },
        },
        "required": ["action_description"],
    },
}

# Hardcoded NY S9365A compliance rules as fallback if Nia is unavailable.
_LOCAL_RULES = [
    "The AI agent MUST identify itself as an automated system at the start of every call.",
    "The reservation MUST be made on behalf of a named human customer (not for resale).",
    "The agent MUST NOT claim to be human or impersonate restaurant staff.",
    "The agent MUST NOT use deceptive or misleading language about the caller's identity.",
    "The reservation MUST NOT be sold, transferred, or brokered to a third party.",
    "The agent MUST state the customer's full name for whom the reservation is being made.",
]


async def check_legal_compliance(
    action_description: str,
    is_for_resale: bool = False,
) -> dict[str, Any]:
    cfg = get_settings()

    if is_for_resale:
        return {
            "approved": False,
            "reason": (
                "BLOCKED: NY S9365A prohibits reservations made for resale or brokering. "
                "ReservaDirect only makes reservations for direct customer use."
            ),
            "rules_applied": _LOCAL_RULES,
        }

    if cfg.stub_external_apis or not cfg.nia_api_key:
        return _local_compliance_check(action_description)

    # Query Nia MCP server
    try:
        result = await _query_nia(cfg, action_description)
        return result
    except Exception as exc:
        logger.warning("Nia MCP unavailable, using local rules: %s", exc)
        return _local_compliance_check(action_description)


async def _query_nia(cfg, action_description: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{cfg.nia_mcp_url}/query",
            json={
                "query": (
                    f"Does the following action comply with NY Senate Bill S9365A "
                    f"(Restaurant Reservation Anti-Piracy Act) and FTC regulations on "
                    f"AI-generated calls? Action: {action_description}"
                ),
                "index": "ny-s9365a-restaurant-law",
                "top_k": 5,
            },
            headers={
                "Authorization": f"Bearer {cfg.nia_api_key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    answer = data.get("answer", "")
    compliant = "not comply" not in answer.lower() and "violation" not in answer.lower()

    return {
        "approved": compliant,
        "reason": answer,
        "rules_applied": data.get("sources", _LOCAL_RULES),
        "source": "nia_mcp",
    }


def _local_compliance_check(action_description: str) -> dict[str, Any]:
    """
    Lightweight local rule check used when Nia is unavailable.
    Flags obvious violations without needing the MCP server.
    """
    action_lower = action_description.lower()

    red_flags = [
        ("resell", "Reservation resale violates NY S9365A."),
        ("broker", "Brokering reservations violates NY S9365A."),
        ("scalp", "Scalping reservations violates NY S9365A."),
        ("pretend to be human", "AI must disclose its nature per FTC guidelines."),
        ("impersonat", "Impersonation is prohibited."),
    ]

    for keyword, reason in red_flags:
        if keyword in action_lower:
            return {"approved": False, "reason": reason, "rules_applied": _LOCAL_RULES}

    return {
        "approved": True,
        "reason": (
            "Action appears compliant with NY S9365A. "
            "Reminder: The voice agent MUST identify itself as AI at the start of the call "
            "and state it is calling on behalf of the named customer."
        ),
        "rules_applied": _LOCAL_RULES,
        "source": "local_fallback",
        "compliance_checklist": [
            "✓ Identify as AI at call start",
            "✓ State customer name",
            "✓ Not for resale",
            "✓ No deceptive language",
        ],
    }
