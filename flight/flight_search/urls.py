"""Build Google Flights search URLs."""

from datetime import date, timedelta
from .config import CABIN_MINIMUM


def build_url(
    origin: str,
    dest: str,
    depart_date: date,
    return_date: date | None = None,
    one_way: bool = False,
    cabin: str | None = None,
    passengers: int = 1,
) -> str:
    """Build a Google Flights URL with ?q= parameter."""
    cabin = cabin or CABIN_MINIMUM
    parts = [
        "Flights",
        "from", origin,
        "to", dest,
        "on", depart_date.isoformat(),
    ]

    if return_date and not one_way:
        parts.extend(["returning", return_date.isoformat()])
    elif one_way or return_date is None:
        parts.extend(["one", "way"])

    # Cabin class — always business minimum
    parts.extend([cabin, "class"])

    if passengers > 1:
        parts.extend([str(passengers), "passengers"])

    query = "+".join(parts)
    return f"https://www.google.com/travel/flights?q={query}"


def build_date_range_urls(
    origin: str,
    dest: str,
    start_date: date,
    num_days: int,
    one_way: bool = True,
    cabin: str | None = None,
) -> list[tuple[str, str, date]]:
    """
    Build URLs for a range of dates.

    Returns list of (session_name, url, search_date) tuples.
    """
    results = []
    for i in range(num_days):
        d = start_date + timedelta(days=i)
        session_name = f"d{i+1}"
        url = build_url(origin, dest, d, one_way=one_way, cabin=cabin)
        results.append((session_name, url, d))
    return results
