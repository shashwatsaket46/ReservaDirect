from anthropic import Anthropic
from dotenv import load_dotenv
from pathlib import Path
import os
import json

# Load root .env manually
env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=env_path)

def parse_reservation(description: str):

    # Create Claude client ONLY when function runs
    client = Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY")
    )

    print("Claude KEY INSIDE FUNC:", os.getenv("ANTHROPIC_API_KEY"))

    prompt = f"""
Extract reservation details from this restaurant booking message.

Return ONLY valid JSON in this exact schema:

{{
 "guest_name": "",
 "phone_number": "",
 "number_of_people": 0,
 "special_request": ""
}}

Message:
{description}
"""

    response = client.messages.create(
        model="claude-3-haiku-latest",
        max_tokens=200,
        temperature=0,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )

    return json.loads(response.content[0].text)