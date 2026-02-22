import googlemaps
import os

def get_gmaps_client():
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")

    if not api_key:
        raise ValueError("GOOGLE_MAPS_API_KEY not set in environment")

    return googlemaps.Client(key=api_key)

def get_restaurant_phone(gmaps, place_id):

    try:
        details = gmaps.place(
            place_id=place_id,
            fields=[
                "name",
                "formatted_phone_number",
                "international_phone_number"
            ]
        )

        result = details.get("result", {})

        return (
                result.get("international_phone_number")
                or result.get("formatted_phone_number")
        )

    except Exception as e:
        print("Phone Fetch Error:", e)
        return None

def get_nearby_restaurants(location: str, radius=2000):

    gmaps = get_gmaps_client()
    geocode_result = gmaps.geocode(location)

    if not geocode_result:
        return {"restaurants": []}

    lat = geocode_result[0]["geometry"]["location"]["lat"]
    lng = geocode_result[0]["geometry"]["location"]["lng"]

    # STEP 2: Nearby restaurant search
    places_result = gmaps.places_nearby(
        location=(lat, lng),
        radius=radius,
        type="restaurant"
    )
    def price_label(level):
        if level == 0:
            return "Free"
        elif level == 1:
            return "Cheap"
        elif level == 2:
            return "Moderate"
        elif level == 3:
            return "Expensive"
        elif level == 4:
            return "Luxury"
        else:
            return "Unknown"

    restaurants = []

    for place in places_result.get("results", [])[:1]:

        place_id = place.get("place_id")

        phone = get_restaurant_phone(gmaps, place_id)

        restaurants.append({
            "name": place.get("name"),
            "rating": place.get("rating"),
            "address": place.get("vicinity"),
            "open_now": place.get("opening_hours", {}).get("open_now"),
            "price_level": place.get("price_level") if place.get("price_level") is not None else -1,
            "price_range": price_label(place.get("price_level")),
            "place_id": place_id,
            "phone_number": phone
        })

    return {
        "location": location,
        "restaurants": restaurants
    }
def search_restaurant(location: str):
    return get_nearby_restaurants(location)