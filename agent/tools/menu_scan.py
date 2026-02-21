"""
Menu scan tool — NVIDIA Nemotron OCR NIM.
Scans a restaurant menu image URL and extracts:
- Walk-in policy
- Hidden fees (service charges, tasting menu requirements)
- Deposit or prepayment requirements
"""

import httpx
import logging
import re
from typing import Any

from agent.config import get_settings

logger = logging.getLogger(__name__)

SCAN_MENU_SCHEMA = {
    "name": "scan_menu",
    "description": (
        "Scan a restaurant menu or policy image using NVIDIA Nemotron OCR. "
        "Extracts walk-in policies, hidden fees, mandatory gratuity, "
        "and prepayment requirements. "
        "Use this when you have a menu image URL to check for surprises before booking."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "Public URL of a menu or policy page image (JPEG/PNG/WebP)",
            },
            "restaurant_name": {"type": "string"},
        },
        "required": ["image_url", "restaurant_name"],
    },
}

# Keywords to flag in extracted text
POLICY_PATTERNS = {
    "deposit_required": r"deposit|prepay|prepaid|hold required|credit card required",
    "walk_ins_accepted": r"walk.?in(s)? welcome|walk.?ins accepted|no reservation",
    "no_walk_ins": r"reservation(s)? only|no walk.?in|walk.?ins not",
    "mandatory_gratuity": r"(mandatory|automatic|auto).{0,20}(gratuity|tip|service charge)",
    "cancellation_fee": r"cancellation fee|no.?show fee|late cancellation",
    "tasting_menu_required": r"tasting menu only|prix fixe only",
}


async def scan_menu(image_url: str, restaurant_name: str) -> dict[str, Any]:
    cfg = get_settings()

    if cfg.stub_external_apis:
        return _stub_result(restaurant_name)

    api_key = cfg.check_key("nvidia_api_key")

    # NVIDIA NIM — Nemotron OCR
    # Docs: https://build.nvidia.com/nvidia/nemotron-ocr
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            json={
                "model": "nvidia/nemotron-ocr-v1",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": image_url},
                            },
                            {
                                "type": "text",
                                "text": (
                                    "Extract all text from this restaurant menu or policy document. "
                                    "Return the full text verbatim, preserving line breaks."
                                ),
                            },
                        ],
                    }
                ],
                "max_tokens": 2048,
                "temperature": 0.0,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    extracted_text = data["choices"][0]["message"]["content"]
    flags = _detect_policies(extracted_text)

    return {
        "restaurant_name": restaurant_name,
        "extracted_text_preview": extracted_text[:500],
        "flags": flags,
        "requires_deposit": flags.get("deposit_required", False),
        "walk_ins_welcome": flags.get("walk_ins_accepted", False),
        "has_mandatory_gratuity": flags.get("mandatory_gratuity", False),
        "has_cancellation_fee": flags.get("cancellation_fee", False),
    }


def _detect_policies(text: str) -> dict[str, bool]:
    text_lower = text.lower()
    return {
        key: bool(re.search(pattern, text_lower, re.IGNORECASE))
        for key, pattern in POLICY_PATTERNS.items()
    }


def _stub_result(restaurant_name: str) -> dict:
    return {
        "restaurant_name": restaurant_name,
        "extracted_text_preview": "[STUB] Reservation required. 18% gratuity added to parties of 6+. No walk-ins.",
        "flags": {
            "deposit_required": False,
            "walk_ins_accepted": False,
            "no_walk_ins": True,
            "mandatory_gratuity": True,
            "cancellation_fee": False,
            "tasting_menu_required": False,
        },
        "requires_deposit": False,
        "walk_ins_welcome": False,
        "has_mandatory_gratuity": True,
        "has_cancellation_fee": False,
    }
