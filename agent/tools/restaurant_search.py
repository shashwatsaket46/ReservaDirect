"""
Restaurant search tool.
Primary: Databricks serving endpoint (returns difficulty_score).
Fallback: Google Places API.
"""

import httpx
import logging
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

SEARCH_RESTAURANT_SCHEMA = {
    "name": "search_restaurant",
    "description": (
        "Search for ONE restaurant matching the given location and cuisine preference. "
        "Call this tool ONCE per user turn. Present the result to the user immediately — "
        "do NOT call it multiple times before responding. Only call again if the user rejects "
        "the suggestion (increment result_index by 1 each rejection). "
        "Returns restaurant name, address, phone, rating, and a difficulty_score (0–100) "
        "indicating how hard it is to get a table. Higher score = harder to book."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City or neighbourhood, e.g. 'Midtown Manhattan' or 'Brooklyn'",
            },
            "cuisine": {
                "type": "string",
                "description": "Cuisine type or restaurant name hint, e.g. 'Italian', 'sushi', 'Le Bernardin'",
            },
            "party_size": {
                "type": "integer",
                "description": "Number of diners",
            },
            "result_index": {
                "type": "integer",
                "description": "0-based index for pagination when user rejects previous suggestion",
                "default": 0,
            },
        },
        "required": ["location", "cuisine", "party_size"],
    },
}


async def search_restaurant(
    location: str,
    cuisine: str,
    party_size: int,
    result_index: int = 0,
) -> dict[str, Any]:
    # LLM may pass these as strings — cast defensively
    result_index = int(result_index)
    party_size = int(party_size)
    cfg = get_settings()

    if cfg.stub_external_apis:
        return _stub_result(result_index)

    # ── Primary: Databricks ───────────────────────────────────────────
    _db_host = cfg.databricks_host or ""
    if _db_host and cfg.databricks_token and "your-workspace" not in _db_host:
        try:
            result = await _databricks_search(cfg, location, cuisine, party_size, result_index)
            if result:
                return result
        except Exception as exc:
            logger.warning("Databricks search failed, falling back to Google Places: %s", exc)

    # ── Fallback: Google Places ───────────────────────────────────────
    if cfg.google_places_api_key:
        return await _google_places_search(cfg, location, cuisine, result_index)

    raise RuntimeError(
        "No search backend configured. Set DATABRICKS_HOST+DATABRICKS_TOKEN "
        "or GOOGLE_PLACES_API_KEY in .env"
    )


async def _databricks_search(cfg, location, cuisine, party_size, result_index):
    url = cfg.databricks_host.rstrip("/") + cfg.databricks_restaurant_endpoint
    payload = {
        "inputs": {
            "location": location,
            "cuisine": cuisine,
            "party_size": party_size,
            "top_k": result_index + 1,
        }
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {cfg.databricks_token}"},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("predictions") or data.get("results") or []
    if not results or result_index >= len(results):
        return None

    r = results[result_index]
    return {
        "name": r.get("name", "Unknown"),
        "address": r.get("address", ""),
        "phone": r.get("phone", ""),
        "rating": r.get("rating", 0.0),
        "difficulty_score": r.get("difficulty_score", 50),
        "description": r.get("description", ""),
        "opentable_id": r.get("opentable_id"),
        "resy_id": r.get("resy_id"),
        "source": "databricks",
    }


async def _google_places_search(cfg, location, cuisine, result_index):
    query = f"{cuisine} restaurant in {location}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "https://maps.googleapis.com/maps/api/place/textsearch/json",
            params={"query": query, "key": cfg.google_places_api_key},
        )
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    if not results or result_index >= len(results):
        return {"error": "No restaurants found", "name": None}

    place = results[result_index]
    place_id = place.get("place_id", "")

    # Fetch details for phone number
    phone = ""
    if place_id:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                detail_resp = await client.get(
                    "https://maps.googleapis.com/maps/api/place/details/json",
                    params={
                        "place_id": place_id,
                        "fields": "formatted_phone_number",
                        "key": cfg.google_places_api_key,
                    },
                )
                detail_resp.raise_for_status()
                phone = detail_resp.json().get("result", {}).get("formatted_phone_number", "")
        except Exception:
            pass

    return {
        "name": place.get("name", ""),
        "address": place.get("formatted_address", ""),
        "phone": phone,
        "rating": place.get("rating", 0.0),
        "difficulty_score": _estimate_difficulty(place),
        "description": place.get("editorial_summary", {}).get("overview", ""),
        "opentable_id": None,
        "resy_id": None,
        "source": "google_places",
    }


def _estimate_difficulty(place: dict) -> int:
    """Rough heuristic: high rating + many reviews = harder to book."""
    rating = place.get("rating", 3.0)
    reviews = place.get("user_ratings_total", 0)
    score = int((rating / 5.0) * 50 + min(reviews / 2000 * 50, 50))
    return min(max(score, 10), 95)


def _stub_result(result_index: int) -> dict:
    stubs = [
        {
            "name": "Carbone",
            "address": "181 Thompson St, New York, NY 10012",
            "phone": "+12122548228",
            "rating": 4.7,
            "difficulty_score": 92,
            "description": "Upscale Italian-American classics in a glamorous retro setting.",
            "opentable_id": None,
            "resy_id": "carbone-nyc",
            "source": "stub",
        },
        {
            "name": "Don Angie",
            "address": "103 Greenwich Ave, New York, NY 10014",
            "phone": "+12124154785",
            "rating": 4.5,
            "difficulty_score": 78,
            "description": "Modern Italian-American with inventive dishes in a cosy West Village spot.",
            "opentable_id": "don-angie",
            "resy_id": None,
            "source": "stub",
        },
    ]
    return stubs[result_index % len(stubs)]
