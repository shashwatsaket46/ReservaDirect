from agent.db.mongo import bookings_collection, sync_state_collection
from datetime import datetime


# ---------------- SYNC TOKEN ----------------

def get_sync_token() -> str | None:
    doc = sync_state_collection.find_one({"_id": "calendar_sync"})
    return doc["sync_token"] if doc else None


def save_sync_token(token: str):
    sync_state_collection.update_one(
        {"_id": "calendar_sync"},
        {"$set": {"sync_token": token}},
        upsert=True
    )


def clear_sync_token():
    sync_state_collection.delete_one({"_id": "calendar_sync"})


# ---------------- BOOKINGS ----------------

def upsert_booking(booking: dict):
    now = datetime.utcnow()
    event_id = booking.get("event_id")
    print("[DB] Upserting booking for event_id:", event_id)
    # Use update_one with upsert=True

    bookings_collection.update_one(
        {"event_id": event_id},
        {
            "$set": {**booking, "updated_at": now},
            "$setOnInsert": {
                "created_at": now,
                "status": "confirmed"
            }
        },
        upsert=True
    )
    print(f"[DB] Upserted booking for event {event_id}")


def cancel_booking(event_id: str):
    result = bookings_collection.update_one(
        {"event_id": event_id},
        {"$set": {"status": "cancelled", "updated_at": datetime.utcnow()}}
    )
    if result.matched_count == 0:
        print(f"[DB] No booking found to cancel for event {event_id}")
    else:
        print(f"[DB] Booking cancelled for event {event_id}")


def get_booking(event_id: str) -> dict | None:
    return bookings_collection.find_one({"event_id": event_id}, {"_id": 0})


def get_all_bookings() -> list:
    return list(bookings_collection.find({"status": "confirmed"}, {"_id": 0}))