"""
Digital booking tool — Branch A of the autonomous booking engine.
Attempts to book via OpenTable or Resy using Playwright headless browser.
Returns confirmation_id on success, or status="not_found" to trigger Branch B.
"""

import logging
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

BOOK_DIGITAL_SCHEMA = {
    "name": "book_digital",
    "description": (
        "Attempt to book a table at a restaurant using OpenTable or Resy. "
        "Provide the restaurant's OpenTable or Resy ID. "
        "Returns a confirmation_id if successful, or status='not_available' "
        "if the restaurant isn't on a digital platform or has no availability."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "restaurant_name": {"type": "string"},
            "opentable_id": {
                "type": "string",
                "description": "OpenTable restaurant slug or ID (null if not on OpenTable)",
            },
            "resy_id": {
                "type": "string",
                "description": "Resy venue slug (null if not on Resy)",
            },
            "date": {
                "type": "string",
                "description": "ISO date string YYYY-MM-DD",
            },
            "time": {
                "type": "string",
                "description": "Preferred time, e.g. '7:30 PM'",
            },
            "party_size": {"type": "integer"},
            "user_name": {"type": "string"},
            "user_email": {"type": "string"},
            "user_phone": {"type": "string"},
        },
        "required": ["restaurant_name", "date", "time", "party_size", "user_name"],
    },
}


async def book_digital(
    restaurant_name: str,
    date: str,
    time: str,
    party_size: int,
    user_name: str,
    opentable_id: str | None = None,
    resy_id: str | None = None,
    user_email: str = "",
    user_phone: str = "",
) -> dict[str, Any]:
    cfg = get_settings()

    if cfg.stub_external_apis:
        return _stub_result(opentable_id, resy_id)

    if not opentable_id and not resy_id:
        return {
            "status": "not_available",
            "reason": "Restaurant has no OpenTable or Resy listing.",
        }

    # Try Resy first (often has more NYC restaurants)
    if resy_id:
        result = await _attempt_resy(resy_id, date, time, party_size, user_name, user_email)
        if result["status"] == "confirmed":
            return result

    # Fall back to OpenTable
    if opentable_id:
        result = await _attempt_opentable(opentable_id, date, time, party_size, user_name, user_email)
        if result["status"] == "confirmed":
            return result

    return {
        "status": "not_available",
        "reason": "No availability found on Resy or OpenTable for the requested time.",
    }


async def _attempt_resy(venue_slug, date, time, party_size, user_name, user_email):
    """Use Playwright to book via Resy."""
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            url = f"https://resy.com/cities/ny/{venue_slug}"
            logger.info("Navigating to Resy: %s", url)
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Select date
            await page.wait_for_selector('[data-test="date-picker"]', timeout=10000)
            await page.click('[data-test="date-picker"]')
            # Resy date picker — type the date in the input
            await page.fill('input[name="date"]', date)

            # Select party size
            await page.select_option('[data-test="party-size"]', str(party_size))

            # Find and click the closest available slot to requested time
            await page.wait_for_selector('[data-test="time-slot"]', timeout=10000)
            slots = await page.query_selector_all('[data-test="time-slot"]')
            if not slots:
                await browser.close()
                return {"status": "not_available", "reason": "No Resy time slots found."}

            # Click first available slot
            await slots[0].click()

            # Fill guest details if prompted
            try:
                await page.fill('input[name="firstName"]', user_name.split()[0], timeout=5000)
                if len(user_name.split()) > 1:
                    await page.fill('input[name="lastName"]', user_name.split()[-1])
                if user_email:
                    await page.fill('input[name="email"]', user_email)
            except Exception:
                pass  # Pre-filled from Resy account

            # Submit
            await page.click('[data-test="submit-reservation"]')
            await page.wait_for_selector('[data-test="confirmation-number"]', timeout=15000)

            confirmation = await page.inner_text('[data-test="confirmation-number"]')
            await browser.close()

            return {"status": "confirmed", "platform": "resy", "confirmation_id": confirmation.strip()}

    except Exception as exc:
        logger.warning("Resy booking failed: %s", exc)
        return {"status": "not_available", "reason": str(exc)}


async def _attempt_opentable(restaurant_id, date, time, party_size, user_name, user_email):
    """Use Playwright to book via OpenTable."""
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            url = f"https://www.opentable.com/r/{restaurant_id}"
            logger.info("Navigating to OpenTable: %s", url)
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Party size
            await page.wait_for_selector('[data-test="party-size-picker"]', timeout=10000)
            await page.click(f'[data-test="covers-{party_size}"]')

            # Date — OpenTable uses a date picker
            await page.click('[data-test="date-picker-button"]')
            await page.fill('input[aria-label="Date"]', date)

            # Time
            await page.fill('input[aria-label="Time"]', time)
            await page.click('[data-test="search-button"]')

            # Select first available slot
            await page.wait_for_selector('[data-test="timeslot"]', timeout=10000)
            slots = await page.query_selector_all('[data-test="timeslot"]')
            if not slots:
                await browser.close()
                return {"status": "not_available", "reason": "No OpenTable slots found."}

            await slots[0].click()

            # Guest info
            try:
                await page.fill('[name="firstName"]', user_name.split()[0], timeout=5000)
                if len(user_name.split()) > 1:
                    await page.fill('[name="lastName"]', user_name.split()[-1])
                if user_email:
                    await page.fill('[name="email"]', user_email)
            except Exception:
                pass

            await page.click('[data-test="complete-reservation"]')
            await page.wait_for_selector('[data-test="confirmation"]', timeout=15000)

            confirmation = await page.inner_text('[data-test="confirmation"]')
            await browser.close()

            return {"status": "confirmed", "platform": "opentable", "confirmation_id": confirmation.strip()}

    except Exception as exc:
        logger.warning("OpenTable booking failed: %s", exc)
        return {"status": "not_available", "reason": str(exc)}


def _stub_result(opentable_id, resy_id):
    if resy_id:
        return {
            "status": "confirmed",
            "platform": "resy",
            "confirmation_id": "RESY-STUB-88421",
        }
    if opentable_id:
        return {
            "status": "confirmed",
            "platform": "opentable",
            "confirmation_id": "OT-STUB-99312",
        }
    return {
        "status": "not_available",
        "reason": "No digital platform IDs provided (stub mode).",
    }
