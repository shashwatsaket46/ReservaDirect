from agent.db.mongo import bookings_collection, sync_state_collection

sync_state_collection.delete_many({})
bookings_collection.delete_many({})
print("All cleared!")