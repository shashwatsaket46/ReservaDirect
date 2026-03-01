# 🍽️ ReservaDirect

Autonomous AI-powered restaurant reservation system that books tables anywhere in the world using only your Google Calendar.

ReservaDirect turns your calendar into a personal concierge that can call restaurants on your behalf, confirm bookings, and keep your schedule updated in real time.

---

## 🚀 Inspiration

Booking a table still often means downloading apps, browsing reservation platforms, or making phone calls. Many great local restaurants are not listed on reservation platforms at all.

We wanted to build a system that could make reservations anywhere in the world **without requiring restaurants to adopt new technology**.

What if your calendar could act as your personal concierge and make calls on your behalf?

---

## ✨ What It Does

ReservaDirect transforms Google Calendar into an automated restaurant reservation assistant.

Users simply create a calendar event with:
- 📍 Location  
- 🍜 Cuisine Preference  
- 👥 Party Size  
- 💰 Price Range  

From there, the system:

1. Detects the event using Google Calendar webhooks  
2. Extracts booking preferences using Claude NLP  
3. Fetches nearby restaurants via Google Maps APIs  
4. Ranks restaurants using a Databricks scoring layer  
5. Places an AI-powered voice call using ElevenLabs  
6. Confirms the reservation automatically  

If the top-ranked restaurant cannot confirm the booking, the system automatically retries with the next best option (up to 5 times).

Calendar updates or cancellations trigger follow-up calls automatically.

---

## 🏗️ Architecture

| Layer        | Technology Used |
|-------------|----------------|
| Backend API | FastAPI |
| NLP Parsing | Anthropic Claude API |
| Voice Agent | ElevenLabs |
| Ranking     | Databricks |
| Database    | MongoDB |
| Calendar    | Google Calendar Webhooks |
| UI (Demo)   | Lovable |

---

## 🧠 How It Works

- Event-driven workflow triggered by Google Calendar
- Claude extracts reservation intent from event description
- Restaurant discovery via Google Maps APIs
- Databricks ranks restaurants by preferences
- ElevenLabs places real-time AI voice calls
- MongoDB stores booking state
- Async retry workflow ensures confirmation

---

## ⚙️ API Endpoints

Once running locally:
