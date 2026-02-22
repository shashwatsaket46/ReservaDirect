from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()

# Ensure the URI includes the DB name
uri = os.getenv("MONGO_URI")
if "mongodb.net/?" in uri:
    uri = uri.replace("mongodb.net/?", "mongodb.net/reservation_db?")

client = MongoClient(os.getenv("MONGO_URI"))
db = client.get_default_database() # This picks up 'reservation_db' from the URI automatically

print(f"--- MONGO CONNECTION REPORT ---")
print(f"Target DB: {db.name}")
print(f"Host: {client.address}")
print(f"-------------------------------")

bookings_collection = db["bookings"]
sync_state_collection = db["sync_state"]