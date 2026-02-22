from .booking_digital import book_digital, BOOK_DIGITAL_SCHEMA
from .booking_voice import make_reservation_call, MAKE_RESERVATION_CALL_SCHEMA
from .payment_auth import request_payment_auth, REQUEST_PAYMENT_AUTH_SCHEMA
from .menu_scan import scan_menu, SCAN_MENU_SCHEMA
from .legal_check import check_legal_compliance, CHECK_LEGAL_COMPLIANCE_SCHEMA

def _to_openai(schema: dict) -> dict:
    """
    Convert Anthropic tool schema → OpenAI/NVIDIA NIM tool schema.
    Anthropic: { name, description, input_schema }
    OpenAI:    { type: "function", function: { name, description, parameters } }
    """
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": schema["input_schema"],
        },
    }


ALL_TOOLS = [
    _to_openai(BOOK_DIGITAL_SCHEMA),
    _to_openai(MAKE_RESERVATION_CALL_SCHEMA),
    _to_openai(REQUEST_PAYMENT_AUTH_SCHEMA),
    _to_openai(SCAN_MENU_SCHEMA),
    _to_openai(CHECK_LEGAL_COMPLIANCE_SCHEMA),
]

TOOL_DISPATCH = {
    "book_digital": book_digital,
    "make_reservation_call": make_reservation_call,
    "request_payment_auth": request_payment_auth,
    "scan_menu": scan_menu,
    "check_legal_compliance": check_legal_compliance,
}
