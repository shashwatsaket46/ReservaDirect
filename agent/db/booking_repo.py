from agent.db import mongo
from datetime import datetime


# ---------------- SYNC TOKEN ----------------

def get_sync_token() -> str | None:

    if mongo.sync_state_collection is None:
        print("[DB] sync_state_collection not initialized yet")
        return None

    doc = mongo.sync_state_collection.find_one({"_id": "calendar_sync"})
    return doc["sync_token"] if doc else None


def save_sync_token(token: str):

    if mongo.sync_state_collection is None:
        print("[DB] sync_state_collection not initialized yet")
        return

    mongo.sync_state_collection.update_one(
        {"_id": "calendar_sync"},
        {"$set": {"sync_token": token}},
        upsert=True
    )


def clear_sync_token():

    if mongo.sync_state_collection is None:
        print("[DB] sync_state_collection not initialized yet")
        return

    mongo.sync_state_collection.delete_one({"_id": "calendar_sync"})


# ---------------- BOOKINGS ----------------

def upsert_booking(booking: dict):

    if mongo.bookings_collection is None:
        print("[DB] bookings_collection not initialized yet")
        return

    now = datetime.utcnow()
    event_id = booking.get("event_id")

    print("[DB] Upserting booking for event_id:", event_id)

    mongo.bookings_collection.update_one(
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

    if mongo.bookings_collection is None:
        print("[DB] bookings_collection not initialized yet")
        return

    result = mongo.bookings_collection.update_one(
        {"event_id": event_id},
        {"$set": {"status": "cancelled", "updated_at": datetime.utcnow()}}
    )

    if result.matched_count == 0:
        print(f"[DB] No booking found to cancel for event {event_id}")
    else:
        print(f"[DB] Booking cancelled for event {event_id}")


def get_booking(event_id: str) -> dict | None:

    if mongo.bookings_collection is None:
        print("[DB] bookings_collection not initialized yet")
        return None

    return mongo.bookings_collection.find_one({"event_id": event_id}, {"_id": 0})


def get_all_bookings() -> list:

    if mongo.bookings_collection is None:
        print("[DB] bookings_collection not initialized yet")
        return []

    return list(
        mongo.bookings_collection.find(
            {"status": "confirmed"},
            {"_id": 0}
        )
    )


# ---------------- USERS ----------------

def save_user_google_data(data):

    if mongo.db is None:
        print("[DB] mongo.db not initialized yet")
        return

    mongo.db.users.insert_one({
        "google_refresh_token": data["refresh_token"],
        "restaurant_calendar_id": data["calendar_id"],
        "watch_resource_id": data["resource_id"],
        "watch_channel_id": data["channel_id"]
    })