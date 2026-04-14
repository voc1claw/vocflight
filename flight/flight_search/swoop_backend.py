"""Swoop-powered flight search backend — direct RPC calls, no browser needed."""

import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from .parser import Flight, Layover, Segment
from .config import (
    SWOOP_MAX_RETRIES,
    SWOOP_RETRY_DELAY,
    SWOOP_INTER_SEARCH_DELAY,
    SWOOP_MAX_WORKERS,
    CACHE_DIR,
    CACHE_TTL_SECONDS,
)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(origin: str, dest: str, depart_date: date, cabin: str, max_stops: int | None, return_date: date | None = None) -> str:
    raw = f"{origin}|{dest}|{depart_date.isoformat()}|{cabin}|{max_stops}|{return_date.isoformat() if return_date else 'OW'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}.json")


def _read_cache(key: str) -> list[dict] | None:
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    try:
        mtime = os.path.getmtime(path)
        if time.time() - mtime > CACHE_TTL_SECONDS:
            os.remove(path)
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(key: str, flights: list[Flight]) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    data = []
    for fl in flights:
        data.append({
            "airline": fl.airline,
            "price": fl.price,
            "price_currency": fl.price_currency,
            "price_raw": fl.price_raw,
            "departure_time": fl.departure_time,
            "arrival_time": fl.arrival_time,
            "departure_airport": fl.departure_airport,
            "arrival_airport": fl.arrival_airport,
            "duration": fl.duration,
            "stops": fl.stops,
            "layovers": [
                {"duration": l.duration, "airport": l.airport, "city": l.city, "code": l.code}
                for l in fl.layovers
            ],
            "ref": fl.ref,
            "price_unavailable": fl.price_unavailable,
            "flight_numbers": fl.flight_numbers,
            "aircraft_types": fl.aircraft_types,
            "departure_date": fl.departure_date,
            "is_round_trip": fl.is_round_trip,
            "segments": [
                {
                    "flight_number": s.flight_number,
                    "airline_name": s.airline_name,
                    "aircraft": s.aircraft,
                    "departure_airport": s.departure_airport,
                    "arrival_airport": s.arrival_airport,
                    "departure_time": s.departure_time,
                    "arrival_time": s.arrival_time,
                    "duration": s.duration,
                    "departure_date": s.departure_date,
                    "arrival_date": s.arrival_date,
                    "has_wifi": s.has_wifi,
                    "has_power": s.has_power,
                    "seat_type": s.seat_type,
                }
                for s in fl.segments
            ],
        })
    try:
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def _flights_from_cache(data: list[dict]) -> list[Flight]:
    flights = []
    for d in data:
        flights.append(Flight(
            airline=d["airline"],
            price=d.get("price"),
            price_currency=d.get("price_currency", ""),
            price_raw=d.get("price_raw", ""),
            departure_time=d.get("departure_time", ""),
            arrival_time=d.get("arrival_time", ""),
            departure_airport=d.get("departure_airport", ""),
            arrival_airport=d.get("arrival_airport", ""),
            duration=d.get("duration", ""),
            stops=d.get("stops", 0),
            layovers=[
                Layover(
                    duration=l.get("duration", ""),
                    airport=l.get("airport", ""),
                    city=l.get("city", ""),
                    code=l.get("code", ""),
                )
                for l in d.get("layovers", [])
            ],
            ref=d.get("ref", ""),
            price_unavailable=d.get("price_unavailable", False),
            flight_numbers=d.get("flight_numbers", []),
            aircraft_types=d.get("aircraft_types", []),
            departure_date=d.get("departure_date", ""),
            is_round_trip=d.get("is_round_trip", False),
            segments=[
                Segment(
                    flight_number=s.get("flight_number", ""),
                    airline_name=s.get("airline_name", ""),
                    aircraft=s.get("aircraft", ""),
                    departure_airport=s.get("departure_airport", ""),
                    arrival_airport=s.get("arrival_airport", ""),
                    departure_time=s.get("departure_time", ""),
                    arrival_time=s.get("arrival_time", ""),
                    duration=s.get("duration", ""),
                    departure_date=s.get("departure_date", ""),
                    arrival_date=s.get("arrival_date", ""),
                    has_wifi=s.get("has_wifi", False),
                    has_power=s.get("has_power", False),
                    seat_type=s.get("seat_type", ""),
                )
                for s in d.get("segments", [])
            ],
        ))
    return flights


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def _mins_to_duration_str(minutes: int) -> str:
    """Convert total minutes to 'X hr Y min' format."""
    h = minutes // 60
    m = minutes % 60
    if m == 0:
        return f"{h} hr"
    return f"{h} hr {m} min"


def _format_time(time_tuple) -> str:
    """Convert (hour, minute) tuple to '1:20 AM' format."""
    if not time_tuple or not hasattr(time_tuple, '__len__') or len(time_tuple) < 2:
        return ""
    h, m = time_tuple[0], time_tuple[1]
    if h is None:
        h = 0
    if m is None:
        m = 0
    period = "AM" if h < 12 else "PM"
    display_h = h % 12
    if display_h == 0:
        display_h = 12
    return f"{display_h}:{m:02d} {period}"


def _swoop_itinerary_to_flight(option, leg_index: int = 0, search_date: date | None = None) -> Flight | None:
    """Convert a swoop TripOption into our Flight dataclass."""
    if not option.legs or leg_index >= len(option.legs):
        return None

    leg = option.legs[leg_index]
    itin = leg.itinerary
    if itin is None:
        return None

    # Price
    price = option.price
    currency = option.currency or "USD"
    currency_symbol = "$" if currency == "USD" else currency
    price_raw = f"{price} {currency}" if price else ""
    price_unavailable = price is None

    # Airlines
    airline_names = itin.airline_names or []
    airline = " + ".join(airline_names) if airline_names else "Unknown"

    # Duration
    duration = _mins_to_duration_str(itin.travel_time) if itin.travel_time else ""

    # Stops
    stop_count = itin.stop_count or 0

    # Departure/arrival
    dep_time = _format_time(itin.departure_time) if itin.departure_time else ""
    arr_time = _format_time(itin.arrival_time) if itin.arrival_time else ""
    dep_airport = itin.departure_airport_code or leg.origin or ""
    arr_airport = itin.arrival_airport_code or leg.destination or ""

    # Departure date string
    dep_date_str = ""
    if itin.departure_date:
        try:
            dep_d = date(itin.departure_date[0], itin.departure_date[1], itin.departure_date[2])
            dep_date_str = dep_d.isoformat()
        except (TypeError, ValueError, IndexError):
            pass
    if not dep_date_str and search_date:
        dep_date_str = search_date.isoformat()

    # +N day indicator
    if arr_time and itin.departure_date and itin.arrival_date:
        try:
            dep_d = date(itin.departure_date[0], itin.departure_date[1], itin.departure_date[2])
            arr_d = date(itin.arrival_date[0], itin.arrival_date[1], itin.arrival_date[2])
            day_diff = (arr_d - dep_d).days
            if day_diff > 0:
                arr_time = f"{arr_time}+{day_diff}"
        except (TypeError, ValueError, IndexError):
            pass

    # Layovers
    layovers = []
    for lay in (itin.layovers or []):
        lay_dur = _mins_to_duration_str(lay.minutes)
        lay_code = lay.arrival_airport_code or ""
        lay_city = lay.arrival_airport_city or ""
        lay_airport_name = lay.arrival_airport_name or ""
        layovers.append(Layover(
            duration=lay_dur,
            airport=lay_airport_name,
            city=lay_city,
            code=lay_code,
        ))

    # Flight numbers, aircraft types, and full segment details
    flight_numbers = []
    aircraft_types = []
    segments = []
    for seg in (itin.segments or []):
        airline_code = seg.airline or ""
        fn = seg.flight_number or ""
        fn_str = f"{airline_code}{fn}" if airline_code and fn else ""
        if fn_str:
            flight_numbers.append(fn_str)
        aircraft = getattr(seg, "aircraft", None) or getattr(seg, "aircraft_type", None) or ""
        if aircraft:
            aircraft_types.append(str(aircraft))

        # Per-segment times and airports
        seg_dep_time = _format_time(seg.departure_time) if getattr(seg, "departure_time", None) else ""
        seg_arr_time = _format_time(seg.arrival_time) if getattr(seg, "arrival_time", None) else ""

        seg_dep_date = ""
        if getattr(seg, "departure_date", None):
            try:
                seg_dep_date = date(seg.departure_date[0], seg.departure_date[1], seg.departure_date[2]).isoformat()
            except (TypeError, ValueError, IndexError):
                pass

        seg_arr_date = ""
        if getattr(seg, "arrival_date", None):
            try:
                seg_arr_date = date(seg.arrival_date[0], seg.arrival_date[1], seg.arrival_date[2]).isoformat()
            except (TypeError, ValueError, IndexError):
                pass

        # +N day indicator on segment arrival
        if seg_arr_time and seg_dep_date and seg_arr_date:
            try:
                dep_d = date.fromisoformat(seg_dep_date)
                arr_d = date.fromisoformat(seg_arr_date)
                day_diff = (arr_d - dep_d).days
                if day_diff > 0:
                    seg_arr_time = f"{seg_arr_time}+{day_diff}"
            except ValueError:
                pass

        seg_duration = _mins_to_duration_str(seg.travel_time) if getattr(seg, "travel_time", None) else ""

        # Amenities
        amenities = getattr(seg, "amenities", None)
        has_wifi = bool(amenities and getattr(amenities, "wifi", 0))
        has_power = bool(amenities and getattr(amenities, "has_power", False))

        # Seat type
        seat_type_code = getattr(seg, "seat_type", None)
        seat_type_map = {1: "Standard seat", 2: "Extra legroom", 3: "Extra reclining seat",
                         4: "Angled flat seat", 5: "Lie-flat seat", 6: "Lie-flat seat"}
        seat_type = seat_type_map.get(seat_type_code, "") if seat_type_code else ""

        segments.append(Segment(
            flight_number=fn_str,
            airline_name=getattr(seg, "airline_name", "") or "",
            aircraft=str(aircraft) if aircraft else "",
            departure_airport=getattr(seg, "departure_airport_code", "") or "",
            arrival_airport=getattr(seg, "arrival_airport_code", "") or "",
            departure_time=seg_dep_time,
            arrival_time=seg_arr_time,
            duration=seg_duration,
            departure_date=seg_dep_date,
            arrival_date=seg_arr_date,
            has_wifi=has_wifi,
            has_power=has_power,
            seat_type=seat_type,
        ))

    ref = ",".join(flight_numbers)

    return Flight(
        airline=airline,
        price=float(price) if price is not None else None,
        price_currency=currency_symbol,
        price_raw=price_raw,
        departure_time=dep_time,
        arrival_time=arr_time,
        departure_airport=dep_airport,
        arrival_airport=arr_airport,
        duration=duration,
        stops=stop_count,
        layovers=layovers,
        ref=ref,
        price_unavailable=price_unavailable,
        flight_numbers=flight_numbers,
        aircraft_types=aircraft_types,
        departure_date=dep_date_str,
        segments=segments,
    )


# ---------------------------------------------------------------------------
# Core search with retry
# ---------------------------------------------------------------------------

def search_swoop(
    origin: str,
    dest: str,
    depart_date: date,
    max_stops: int | None = None,
    cabin: str = "business",
    use_cache: bool = True,
) -> list[Flight]:
    """Search flights using swoop with retry and caching."""
    # Check cache first
    cache_k = _cache_key(origin, dest, depart_date, cabin, max_stops)
    if use_cache:
        cached = _read_cache(cache_k)
        if cached is not None:
            print(f"  [cache hit: {origin}->{dest} {depart_date}]", file=sys.stderr)
            return _flights_from_cache(cached)

    try:
        from swoop import search
    except ImportError:
        print("swoop-flights not installed. Install with: pip install swoop-flights", file=sys.stderr)
        return []

    last_err = None
    for attempt in range(SWOOP_MAX_RETRIES):
        try:
            results = search(
                origin,
                dest,
                depart_date.isoformat(),
                cabin=cabin,
                max_stops=max_stops,
            )

            flights = []
            for option in results.results:
                flight = _swoop_itinerary_to_flight(option, search_date=depart_date)
                if flight:
                    flights.append(flight)

            # Cache the results
            if use_cache and flights:
                _write_cache(cache_k, flights)

            return flights

        except Exception as e:
            last_err = e
            delay = SWOOP_RETRY_DELAY * (2 ** attempt)
            print(f"  [swoop attempt {attempt + 1}/{SWOOP_MAX_RETRIES} failed: {e}, retry in {delay:.1f}s]", file=sys.stderr)
            if attempt < SWOOP_MAX_RETRIES - 1:
                time.sleep(delay)

    print(f"Swoop search failed after {SWOOP_MAX_RETRIES} attempts: {last_err}", file=sys.stderr)
    return []


def search_swoop_roundtrip(
    origin: str,
    dest: str,
    depart_date: date,
    return_date: date,
    max_stops: int | None = None,
    cabin: str = "business",
    use_cache: bool = True,
) -> list[Flight]:
    """Search round-trip flights. Returns outbound flights with RT total price."""
    cache_k = _cache_key(origin, dest, depart_date, cabin, max_stops, return_date=return_date)
    if use_cache:
        cached = _read_cache(cache_k)
        if cached is not None:
            print(f"  [cache hit: {origin}->{dest} RT {depart_date}/{return_date}]", file=sys.stderr)
            return _flights_from_cache(cached)

    try:
        from swoop import search
    except ImportError:
        print("swoop-flights not installed. Install with: pip install swoop-flights", file=sys.stderr)
        return []

    last_err = None
    for attempt in range(SWOOP_MAX_RETRIES):
        try:
            results = search(
                origin,
                dest,
                depart_date.isoformat(),
                return_date=return_date.isoformat(),
                cabin=cabin,
                max_stops=max_stops,
            )

            flights = []
            for option in results.results:
                flight = _swoop_itinerary_to_flight(option, search_date=depart_date)
                if flight:
                    flight.is_round_trip = True
                    flights.append(flight)

            if use_cache and flights:
                _write_cache(cache_k, flights)

            return flights

        except Exception as e:
            last_err = e
            delay = SWOOP_RETRY_DELAY * (2 ** attempt)
            print(f"  [swoop RT attempt {attempt + 1}/{SWOOP_MAX_RETRIES} failed: {e}, retry in {delay:.1f}s]", file=sys.stderr)
            if attempt < SWOOP_MAX_RETRIES - 1:
                time.sleep(delay)

    print(f"Swoop RT search failed after {SWOOP_MAX_RETRIES} attempts: {last_err}", file=sys.stderr)
    return []


# ---------------------------------------------------------------------------
# Parallel multi-date search
# ---------------------------------------------------------------------------

def search_swoop_parallel(
    origin: str,
    dest: str,
    dates: list[date],
    max_stops: int | None = None,
    cabin: str = "business",
    use_cache: bool = True,
) -> dict[date, list[Flight]]:
    """Search multiple dates in parallel using a thread pool."""
    results: dict[date, list[Flight]] = {}

    def _search_one(d: date) -> tuple[date, list[Flight]]:
        flights = search_swoop(origin, dest, d, max_stops=max_stops, cabin=cabin, use_cache=use_cache)
        time.sleep(SWOOP_INTER_SEARCH_DELAY)
        return d, flights

    with ThreadPoolExecutor(max_workers=SWOOP_MAX_WORKERS) as pool:
        futures = {pool.submit(_search_one, d): d for d in dates}
        for future in as_completed(futures):
            try:
                d, flights = future.result()
                results[d] = flights
            except Exception as e:
                d = futures[future]
                print(f"  [parallel search error for {d}: {e}]", file=sys.stderr)
                results[d] = []

    return results


def search_swoop_roundtrip_parallel(
    origin: str,
    dest: str,
    date_pairs: list[tuple[date, date]],
    max_stops: int | None = None,
    cabin: str = "business",
    use_cache: bool = True,
) -> dict[date, list[Flight]]:
    """Search multiple RT date pairs in parallel. Keyed by departure date."""
    results: dict[date, list[Flight]] = {}

    def _search_one(dep: date, ret: date) -> tuple[date, list[Flight]]:
        flights = search_swoop_roundtrip(origin, dest, dep, ret, max_stops=max_stops, cabin=cabin, use_cache=use_cache)
        time.sleep(SWOOP_INTER_SEARCH_DELAY)
        return dep, flights

    with ThreadPoolExecutor(max_workers=SWOOP_MAX_WORKERS) as pool:
        futures = {pool.submit(_search_one, dep, ret): dep for dep, ret in date_pairs}
        for future in as_completed(futures):
            try:
                d, flights = future.result()
                results[d] = flights
            except Exception as e:
                d = futures[future]
                print(f"  [parallel RT search error for {d}: {e}]", file=sys.stderr)
                results[d] = []

    return results
