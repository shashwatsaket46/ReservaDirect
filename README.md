# 🍽️ ReservaDirect

Autonomous AI-powered restaurant reservation system that books tables anywhere in the world using only your Google Calendar.

ReservaDirect turns your calendar into a personal concierge that can call restaurants on your behalf, confirm bookings, and keep your schedule updated in real time.

---

## 🚀 Inspiration

Booking a table still often means downloading apps, browsing reservation platforms, or making phone calls. Many great local restaurants are not listed on reservation platforms at all.

We wanted to build a system that could make reservations anywhere in the world **without requiring restaurants to adopt new technology**.

What if your calendar could act as your personal concierge and make calls on your behalf?

---

The reserva direct website looks like this where you can create your account and then add the calender required for further processing.
<img width="980" height="522" alt="image" src="https://github.com/user-attachments/assets/c8974664-7aea-41fe-bd92-2f5acc3fb60b" />
<img width="978" height="465" alt="image" src="https://github.com/user-attachments/assets/cacee935-8288-4757-b908-f7d275010d2a" />
<table align="center">
<tr>
<td align="center" valign="top">
<img src="https://github.com/user-attachments/assets/1446a9b1-8f63-4c89-a27c-632679371b54" width="250"/><br/>
<b>Calendar Event Input</b>
</td>

<td align="center" valign="top">
<img src="https://github.com/user-attachments/assets/d215c2ae-244e-4401-a085-696168961cba" width="250"/><br/>
<b>Reservation Confirmation</b>
</td>
</tr>
</table>


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


Available endpoints include:

- `/google/login`
- `/calendar/book`
- `/calendar/webhook`
- `/api/setup-intent`
- `/api/message`
- `/api/cancel-reservation`
- `/webhook/elevenlabs/call-result`

---

## 🧩 Challenges

- Handling duplicate Google webhook notifications  
- Preventing reservation calls during event edits  
- Managing async voice workflows with real-time calendar sync  
- Building reliable booking stabilization logic  

---

## 🏆 Accomplishments

- End-to-end reservation workflow  
- Works without requiring restaurants to join any platform  
- Automated make / update / cancel reservations  
- Global compatibility using phone calls  

---

## 📚 What We Learned

- Designing webhook-driven systems  
- Async orchestration for real-world automation  
- ML-ready ranking pipelines  
- Integrating AI voice agents into live workflows  

---

## 🛠️ Built With

- FastAPI  
- Anthropic Claude  
- ElevenLabs  
- Databricks  
- MongoDB  
- Google Calendar API  
- Lovable  

---

## 🖥️ Demo UI

https://calendar-ai-reservations.lovable.app/

---

## 📦 Getting Started

```bash
git clone https://github.com/shashwatsaket46/ReservaDirect.git
cd ReservaDirect
pip install -r requirements.txt
uvicorn main:app --reload

Once running locally:




