import random
from datetime import datetime

def rank_restaurants_via_databricks(booking, restaurants):
    """
    Dummy Databricks Ranking

    Pretends to predict:
    P(Reservation Accepted | restaurant, booking)
    """

    ranked = []

    for r in restaurants:
        rating = r.get("rating", 3.5)
        party = booking.get("number_of_people") or 2

        hour = int(booking["time"].split(":")[0])
        weekday = datetime.strptime(
            booking["date"], "%d/%b/%Y"
        ).weekday()
        score = (
                (rating * 0.5) +
                (1 if party <= 4 else 0.3) +
                (0.5 if 18 <= hour <= 21 else 0.2) +
                (0.3 if weekday < 5 else 0.1) +
                random.uniform(0, 0.2)
        )

        ranked.append((r, score))

    ranked.sort(key=lambda x: x[1], reverse=True)

    best = ranked[0][0]

    print(f"[DATABRICKS MOCK] Selected: {best.get('name')}")

    return best