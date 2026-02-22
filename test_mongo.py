import os
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
from dotenv import load_dotenv

load_dotenv()

def test_connection():
    uri = os.getenv("MONGO_URI")
    print(f"Testing connection to: {uri.split('@')[-1]}") # Prints host, hides password

    try:
        # 1. Connect and Ping
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        print("✅ SUCCESS: Connection established and Cluster pinged.")

        # 2. Check Database Access
        db = client.get_default_database()
        print(f"✅ SUCCESS: Accessing Database: '{db.name}'")

        # 3. Test Write/Delete Permission
        test_collection = db["connection_test"]
        test_doc = {"test": "ping", "timestamp": "now"}

        insert_result = test_collection.insert_one(test_doc)
        print(f"✅ SUCCESS: Write permission verified. Doc ID: {insert_result.inserted_id}")

        test_collection.delete_one({"_id": insert_result.inserted_id})
        print("✅ SUCCESS: Delete permission verified.")

        # 4. Count existing bookings
        booking_count = db["bookings"].count_documents({})
        print(f"📊 REPORT: Found {booking_count} documents in the 'bookings' collection.")

    except ConnectionFailure:
        print("❌ ERROR: Could not connect to MongoDB. Check your IP Whitelist in Atlas.")
    except OperationFailure as e:
        print(f"❌ ERROR: Authentication or Database error: {e}")
    except Exception as e:
        print(f"❌ ERROR: An unexpected error occurred: {e}")

if __name__ == "__main__":
    test_connection()