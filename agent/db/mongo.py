from pymongo import MongoClient
import os
import certifi
from dotenv import load_dotenv

load_dotenv()

client = None
db = None
bookings_collection = None
sync_state_collection = None


def init_mongo():

    global client, db
    global bookings_collection, sync_state_collection

    uri = os.getenv("MONGO_URI")

    if "mongodb.net/?" in uri:
        uri = uri.replace("mongodb.net/?", "mongodb.net/reservation_db?")

    client = MongoClient(
        uri,
        tls=True,
        tlsCAFile=certifi.where()
    )

    db = client.get_default_database()

    bookings_collection = db["bookings"]
    sync_state_collection = db["sync_state"]

    try:
        client.admin.command("ping")
        print("Mongo Connected ✅")
        print(f"Target DB: {db.name}")
    except Exception as e:
        print("Mongo Connection Failed ❌", e)