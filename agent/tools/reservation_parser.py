from anthropic import Anthropic
from dotenv import load_dotenv
from pathlib import Path
import os
import json

# Load root .env manually
env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=env_path)

def parse_reservation(description: str):

    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    prompt = f"""
Extract reservation details from this restaurant booking message.

Return ONLY valid JSON with no markdown, no code blocks, no explanation.

Exact schema:
{{
  "phone_number": "",
  "number_of_people": 0,
  "price_range": "",
  "special_request": ""
}}

Message:
{description}
"""

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        temperature=0,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()

    # Strip markdown code blocks if Claude wraps the JSON
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw)