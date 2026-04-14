"""Apply mandatory rules: banned airlines, max stops, max layover, routing exclusions."""

from .config import BANNED_AIRLINES
from .parser import Flight, parse_layover_duration_hours


def filter_banned_airlines(flights: list[Flight]) -> list[Flight]:
    """Remove flights operated by banned airlines."""
    result = []
    for f in flights:
        airline_lower = f.airline.lower()
        is_banned = False
        for banned in BANNED_AIRLINES:
            if banned in airline_lower:
                is_banned = True
                break
        if not is_banned:
            result.append(f)
    return result


def filter_max_stops(flights: list[Flight], max_stops: int) -> list[Flight]:
    """Remove flights exceeding max stops."""
    return [f for f in flights if f.stops <= max_stops]


def filter_max_layover(flights: list[Flight], max_hours: float) -> list[Flight]:
    """Remove flights with any layover exceeding max_hours."""
    result = []
    for f in flights:
        exceeds = False
        for lay in f.layovers:
            if parse_layover_duration_hours(lay.duration) > max_hours:
                exceeds = True
                break
        if not exceeds:
            result.append(f)
    return result


def filter_excluded_routing(
    flights: list[Flight], excluded_codes: set[str]
) -> list[Flight]:
    """Remove flights that route through excluded airport codes."""
    if not excluded_codes:
        return flights
    excluded_upper = {c.upper() for c in excluded_codes}
    result = []
    for f in flights:
        routes_through_excluded = False
        for lay in f.layovers:
            if lay.code.upper() in excluded_upper:
                routes_through_excluded = True
                break
        if not routes_through_excluded:
            result.append(f)
    return result


def apply_all_filters(
    flights: list[Flight],
    max_stops: int | None = None,
    max_layover_hours: float | None = None,
    excluded_routing: set[str] | None = None,
) -> list[Flight]:
    """Apply all filters in order."""
    result = filter_banned_airlines(flights)

    if max_stops is not None:
        result = filter_max_stops(result, max_stops)

    if max_layover_hours is not None:
        result = filter_max_layover(result, max_layover_hours)

    if excluded_routing:
        result = filter_excluded_routing(result, excluded_routing)

    return result
