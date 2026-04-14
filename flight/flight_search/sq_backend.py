"""Singapore Airlines flight search backend — browser-based scraping via agent-browser.

Bypasses Akamai bot protection using headed Chromium with AutomationControlled
disabled. Scrapes singaporeair.com search results via DOM extraction.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from .parser import Flight, Layover, Segment
from .config import (
    AGENT_BROWSER,
    CACHE_DIR,
    CACHE_TTL_SECONDS,
)

# SQ-specific constants
SQ_MAX_RETRIES = 2
SQ_RETRY_DELAY = 3.0
SQ_INTER_SEARCH_DELAY = 2.0
SQ_MAX_WORKERS = 2  # conservative — each session is a full headed browser
SQ_LAUNCH_ARGS = "--disable-blink-features=AutomationControlled,--no-sandbox"

# Airport city name mapping for autocomplete
AIRPORT_CITY_NAMES = {
    "BOM": "Mumbai",
    "DEL": "New Delhi",
    "MAA": "Chennai",
    "BLR": "Bangalore",
    "HYD": "Hyderabad",
    "CCU": "Kolkata",
    "LAX": "Los Angeles",
    "SFO": "San Francisco",
    "JFK": "New York",
    "EWR": "Newark",
    "ORD": "Chicago",
    "IAD": "Washington",
    "SIN": "Singapore",
    "NRT": "Tokyo",
    "HND": "Tokyo Haneda",
    "HKG": "Hong Kong",
    "LHR": "London",
    "CDG": "Paris",
    "FRA": "Frankfurt",
    "AMS": "Amsterdam",
    "ZRH": "Zurich",
    "MUC": "Munich",
    "DXB": "Dubai",
    "DOH": "Doha",
    "SYD": "Sydney",
    "MEL": "Melbourne",
    "ICN": "Seoul",
    "TPE": "Taipei",
    "KUL": "Kuala Lumpur",
    "BKK": "Bangkok",
    "CGK": "Jakarta",
    "MNL": "Manila",
    "PVG": "Shanghai",
    "PEK": "Beijing",
    "JNB": "Johannesburg",
    "CPT": "Cape Town",
    "FCO": "Rome",
    "BCN": "Barcelona",
    "MXP": "Milan",
    "SEA": "Seattle",
    "YVR": "Vancouver",
    "ADD": "Addis Ababa",
    "NBO": "Nairobi",
    "CMB": "Colombo",
    "DAC": "Dhaka",
    "KTM": "Kathmandu",
    "RGN": "Yangon",
    "PNH": "Phnom Penh",
    "SGN": "Ho Chi Minh City",
    "HAN": "Hanoi",
    "DPS": "Denpasar Bali",
    "CEB": "Cebu",
    "IST": "Istanbul",
    "AMM": "Amman",
    "CAI": "Cairo",
    "MCT": "Muscat",
    "BAH": "Bahrain",
    "KWI": "Kuwait",
    "RUH": "Riyadh",
    "JED": "Jeddah",
    "AUH": "Abu Dhabi",
}

# Currency conversion rates to USD (approximate, updated periodically)
CURRENCY_TO_USD = {
    "INR": 0.012,
    "SGD": 0.75,
    "EUR": 1.08,
    "GBP": 1.27,
    "AUD": 0.65,
    "JPY": 0.0067,
    "HKD": 0.13,
    "THB": 0.028,
    "MYR": 0.22,
    "KRW": 0.00073,
    "CNY": 0.14,
    "AED": 0.27,
    "USD": 1.0,
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(origin: str, dest: str, depart_date: date, cabin: str,
               max_stops: int | None, return_date: date | None = None) -> str:
    raw = f"SQ|{origin}|{dest}|{depart_date.isoformat()}|{cabin}|{max_stops}|{return_date.isoformat() if return_date else 'OW'}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"sq_{key}.json")


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
# agent-browser helpers
# ---------------------------------------------------------------------------

def _popen_env() -> dict:
    env = os.environ.copy()
    env["MSYS_NO_PATHCONV"] = "1"
    return env


def _ab(session: str, *cmd_args: str, timeout: int = 60) -> tuple[int, str, str]:
    """Run an agent-browser command on a session."""
    args = [AGENT_BROWSER, "--session", session] + list(cmd_args)
    env = _popen_env()
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                timeout=timeout, env=env, encoding="utf-8",
                                errors="replace")
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", f"command not found: {args[0]}"


def _ab_eval(session: str, js: str, timeout: int = 15) -> str:
    """Run JavaScript in the browser and return result."""
    rc, out, err = _ab(session, "eval", js, timeout=timeout)
    if rc != 0:
        return ""
    return out.strip().strip('"')


def _ab_open(session: str, url: str) -> bool:
    """Open URL with headed mode and anti-detection args.

    The headed open command may not return cleanly via subprocess, so we
    fire it off, wait for the page to load, then verify via snapshot.
    """
    args = [AGENT_BROWSER, "--session", session, "--headed",
            "--args", SQ_LAUNCH_ARGS, "open", url]
    env = _popen_env()
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            # Expected — headed open may hang. The browser is running.
            pass
        # Give the page time to load
        time.sleep(6)
        # Verify by checking if we can snapshot
        rc, out, _ = _ab(session, "get", "url", timeout=10)
        return rc == 0 and bool(out.strip())
    except FileNotFoundError:
        return False


def _ab_close(session: str) -> None:
    _ab(session, "close", timeout=10)


def _ab_click_ref(session: str, ref: str) -> bool:
    rc, _, _ = _ab(session, "click", ref, timeout=10)
    return rc == 0


def _ab_fill(session: str, ref: str, text: str) -> bool:
    rc, _, _ = _ab(session, "fill", ref, text, timeout=10)
    return rc == 0


def _ab_find_click(session: str, text: str) -> bool:
    rc, _, _ = _ab(session, "find", "text", text, "click", timeout=10)
    return rc == 0


def _ab_wait(session: str, ms: int) -> None:
    _ab(session, "wait", str(ms), timeout=max(ms // 1000 + 5, 10))


def _ab_snapshot(session: str) -> str:
    rc, out, _ = _ab(session, "snapshot", "-i", timeout=15)
    return out if rc == 0 else ""


def _ab_scroll(session: str, direction: str = "down", px: int = 500) -> None:
    _ab(session, "scroll", direction, str(px), timeout=10)


def _click_autocomplete_suggestion(session: str, iata_code: str, city_name: str) -> bool:
    """Click the SQ airport autocomplete suggestion using agent-browser find text.

    The dropdown items show text like 'Mumbai, India  Chhatrapati Shivaji Intl  BOM'.
    We use agent-browser's native find text command which properly triggers click handlers.
    JavaScript clicks don't reliably trigger SQ's Angular/custom event handlers.
    """
    # Try matching by IATA code (most reliable — appears in every suggestion)
    if _ab_find_click(session, iata_code):
        return True
    # Fallback: match by city name with country context
    if _ab_find_click(session, f"{city_name},"):
        return True
    if _ab_find_click(session, city_name):
        return True
    return False


# ---------------------------------------------------------------------------
# Price parsing and currency conversion
# ---------------------------------------------------------------------------

def _parse_price_to_usd(price_text: str) -> tuple[float | None, str]:
    """Parse SQ price text like 'From INR 816,453 Per adult' -> (USD amount, raw text).

    Returns (price_in_usd, raw_text). Returns (None, raw_text) if unparseable.
    """
    if not price_text or "Select first" in price_text or "Not applicable" in price_text:
        return None, price_text

    # Extract currency and amount: "From INR 816,453 Per adult"
    m = re.search(r"(?:From\s+)?([A-Z]{3})\s+([\d,]+(?:\.\d+)?)", price_text)
    if not m:
        # Try just number: "816,453"
        m2 = re.search(r"([\d,]+(?:\.\d+)?)", price_text)
        if m2:
            amount = float(m2.group(1).replace(",", ""))
            return amount, price_text
        return None, price_text

    currency = m.group(1)
    amount = float(m.group(2).replace(",", ""))
    rate = CURRENCY_TO_USD.get(currency, 1.0)
    usd_amount = round(amount * rate, 0)
    return usd_amount, f"{currency} {m.group(2)}"


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

def _parse_sq_duration(dur_str: str) -> str:
    """Convert '37hrs 10mins' -> '37 hr 10 min'."""
    dur_str = dur_str.strip()
    dur_str = re.sub(r"(\d+)\s*hrs?", r"\1 hr", dur_str)
    dur_str = re.sub(r"(\d+)\s*mins?", r"\1 min", dur_str)
    return dur_str


# ---------------------------------------------------------------------------
# DOM extraction — runs in browser via eval
# ---------------------------------------------------------------------------

EXTRACT_FLIGHTS_JS = r"""
(function() {
    var opts = document.querySelectorAll('.flight-option');
    var results = [];
    opts.forEach(function(opt, idx) {
        var text = opt.textContent.replace(/\s+/g, ' ').trim();

        // Stops and duration
        var stopsMatch = text.match(/(Nonstop|One-stop|Two-stops|[\w-]+stops?)\s*[•·]\s*(\d+hrs?\s*\d*mins?)/i);
        var stopsStr = stopsMatch ? stopsMatch[1] : '';
        var duration = stopsMatch ? stopsMatch[2] : '';

        // Stop count
        var stopCount = 0;
        if (/two/i.test(stopsStr)) stopCount = 2;
        else if (/one/i.test(stopsStr)) stopCount = 1;
        else if (/nonstop/i.test(stopsStr)) stopCount = 0;
        else { var sn = stopsStr.match(/(\d+)/); if (sn) stopCount = parseInt(sn[1]); }

        // Origin/destination from summary station items
        var summaryStations = opt.querySelectorAll('.flight-station-item');
        var depCode = '', depTime = '', depDate = '', depCity = '';
        var arrCode = '', arrTime = '', arrDate = '', arrCity = '';
        if (summaryStations.length > 0) {
            var first = summaryStations[0].textContent.replace(/\s+/g, ' ').trim();
            var m1 = first.match(/^([A-Z]{3})\s+(\d{1,2}:\d{2})\s+(.+?)\s+(\d{1,2}\s+\w+\s+\(\w+\))/);
            if (m1) { depCode = m1[1]; depTime = m1[2]; depCity = m1[3]; depDate = m1[4]; }
        }
        // The last station has arrival info - but it's mixed with layover info
        // Better to get from the detailed segments

        // Flight numbers (unique)
        var fnEls = opt.querySelectorAll('.airline-flight-number');
        var allFns = [];
        fnEls.forEach(function(f) { allFns.push(f.textContent.replace(/\s+/g, '').trim()); });
        // Deduplicate (SQ shows them twice - summary + detail)
        var fnSet = {};
        var fns = [];
        allFns.forEach(function(fn) { if (!fnSet[fn]) { fnSet[fn] = true; fns.push(fn); } });

        // Aircraft types from detail sections
        var aircraftTypes = [];
        var acMatches = text.match(/Boeing\s+[\w-]+|Airbus\s+[\w-]+/gi) || [];
        var acSet = {};
        acMatches.forEach(function(ac) { if (!acSet[ac]) { acSet[ac] = true; aircraftTypes.push(ac); } });

        // Layovers
        var layMatches = [];
        var layRegex = /Layover time:\s*(\d+hrs?\s*\d*mins?)/gi;
        var lm;
        while ((lm = layRegex.exec(text)) !== null) { layMatches.push(lm[1]); }
        // Deduplicate (shown twice)
        var uniqueLays = [];
        var laySet = {};
        layMatches.forEach(function(l) { if (!laySet[l]) { laySet[l] = true; uniqueLays.push(l); } });

        // Layover airports from station summary
        var layCodes = [];
        var layCodeRegex = /([A-Z]{3})\s+\d+h\s*\d*m/g;
        var lcm;
        var stationText = summaryStations.length > 0 ? summaryStations[0].textContent.replace(/\s+/g, ' ') : '';
        while ((lcm = layCodeRegex.exec(stationText)) !== null) { layCodes.push(lcm[1]); }

        // Detailed segments from expanded detail
        var segments = [];
        var segEls = opt.querySelectorAll('[class*=flight-station-item]');
        // Segments come in pairs: first item has dep info, connected to arr info
        // Better approach: parse from the detail text
        var detailBlocks = opt.querySelectorAll('.flight-station');
        detailBlocks.forEach(function(block, bi) {
            if (bi === 0) return; // skip summary block
            var btext = block.textContent.replace(/\s+/g, ' ').trim();
            // Pattern: "BOM 11:45 Mumbai 07 Apr (Tue) ... SIN 19:50 Singapore 07 Apr (Tue) ... SQ 421 Boeing 787-10"
            var segMatch = btext.match(/([A-Z]{3})\s+(\d{1,2}:\d{2})\s+(.+?)\s+(\d{1,2}\s+\w+\s+\(\w+\)).*?([A-Z]{3})\s+(\d{1,2}:\d{2})\s+(.+?)\s+(\d{1,2}\s+\w+\s+\(\w+\))/);
            if (segMatch) {
                var fnMatch = btext.match(/SQ\s*\d+/);
                var acMatch = btext.match(/Boeing\s+[\w-]+|Airbus\s+[\w-]+/i);
                var cabinMatch = btext.match(/Business|First|Suites|Premium Economy|Economy/i);
                segments.push({
                    dep_airport: segMatch[1],
                    dep_time: segMatch[2],
                    dep_city: segMatch[3].trim(),
                    dep_date: segMatch[4],
                    arr_airport: segMatch[5],
                    arr_time: segMatch[6],
                    arr_city: segMatch[7].trim(),
                    arr_date: segMatch[8],
                    flight_number: fnMatch ? fnMatch[0].replace(/\s+/g, '') : '',
                    aircraft: acMatch ? acMatch[0] : '',
                    cabin: cabinMatch ? cabinMatch[0] : '',
                });
            }
        });

        // If no detailed segments parsed, build from summary
        if (segments.length === 0 && depCode) {
            segments.push({
                dep_airport: depCode,
                dep_time: depTime,
                dep_city: depCity,
                dep_date: depDate,
                arr_airport: '',
                arr_time: '',
                arr_city: '',
                arr_date: '',
                flight_number: fns.length > 0 ? fns[0] : '',
                aircraft: aircraftTypes.length > 0 ? aircraftTypes[0] : '',
                cabin: '',
            });
        }

        // Price from fare button
        var priceEl = opt.querySelector('.flight-price, [class*=fare-inner], button[class*=business]');
        var priceText = priceEl ? priceEl.textContent.replace(/\s+/g, ' ').trim() : '';

        // Overall dep/arr from first and last segments
        var overallDep = segments.length > 0 ? segments[0] : null;
        var overallArr = segments.length > 0 ? segments[segments.length - 1] : null;

        results.push({
            index: idx,
            stops: stopCount,
            duration: duration,
            dep_airport: overallDep ? overallDep.dep_airport : depCode,
            dep_time: overallDep ? overallDep.dep_time : depTime,
            dep_date: overallDep ? overallDep.dep_date : depDate,
            arr_airport: overallArr ? overallArr.arr_airport : '',
            arr_time: overallArr ? overallArr.arr_time : '',
            arr_date: overallArr ? overallArr.arr_date : '',
            flight_numbers: fns,
            aircraft_types: aircraftTypes,
            layover_durations: uniqueLays,
            layover_codes: layCodes,
            segments: segments,
            price_text: priceText,
        });
    });
    return JSON.stringify(results);
})()
"""


# ---------------------------------------------------------------------------
# Parse extracted JSON into Flight dataclass
# ---------------------------------------------------------------------------

def _sq_date_to_iso(date_str: str, search_year: int) -> str:
    """Convert '07 Apr (Tue)' -> '2026-04-07'."""
    if not date_str:
        return ""
    m = re.match(r"(\d{1,2})\s+(\w+)\s*\(\w+\)", date_str.strip())
    if not m:
        return ""
    day = int(m.group(1))
    month_str = m.group(2)
    month_map = {
        "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
    }
    month = month_map.get(month_str, 0)
    if month == 0:
        return ""
    return f"{search_year}-{month:02d}-{day:02d}"


def _convert_time_12h(time_24: str) -> str:
    """Convert '14:20' -> '2:20 PM'."""
    if not time_24:
        return ""
    try:
        parts = time_24.split(":")
        h = int(parts[0])
        m = int(parts[1])
        period = "AM" if h < 12 else "PM"
        display_h = h % 12
        if display_h == 0:
            display_h = 12
        return f"{display_h}:{m:02d} {period}"
    except (ValueError, IndexError):
        return time_24


def _parse_extracted_flight(data: dict, search_date: date, is_round_trip: bool = False) -> Flight | None:
    """Convert a single extracted flight dict into a Flight dataclass."""
    if not data:
        return None

    search_year = search_date.year
    stops = data.get("stops", 0)
    duration = _parse_sq_duration(data.get("duration", ""))

    dep_airport = data.get("dep_airport", "")
    dep_time_24 = data.get("dep_time", "")
    dep_time = _convert_time_12h(dep_time_24)
    dep_date_raw = data.get("dep_date", "")
    dep_date_iso = _sq_date_to_iso(dep_date_raw, search_year)

    arr_airport = data.get("arr_airport", "")
    arr_time_24 = data.get("arr_time", "")
    arr_time = _convert_time_12h(arr_time_24)
    arr_date_raw = data.get("arr_date", "")
    arr_date_iso = _sq_date_to_iso(arr_date_raw, search_year)

    # +N day indicator
    if dep_date_iso and arr_date_iso and dep_date_iso != arr_date_iso:
        try:
            dep_d = date.fromisoformat(dep_date_iso)
            arr_d = date.fromisoformat(arr_date_iso)
            day_diff = (arr_d - dep_d).days
            if day_diff > 0:
                arr_time = f"{arr_time}+{day_diff}"
        except ValueError:
            pass

    flight_numbers = [fn.replace(" ", "") for fn in data.get("flight_numbers", [])]
    aircraft_types = data.get("aircraft_types", [])

    # Layovers
    lay_durations = data.get("layover_durations", [])
    lay_codes = data.get("layover_codes", [])
    layovers = []
    for li in range(max(len(lay_durations), len(lay_codes))):
        dur = _parse_sq_duration(lay_durations[li]) if li < len(lay_durations) else ""
        code = lay_codes[li] if li < len(lay_codes) else ""
        layovers.append(Layover(duration=dur, airport="", city="", code=code))

    # Segments
    segments = []
    for seg_data in data.get("segments", []):
        seg_dep_date = _sq_date_to_iso(seg_data.get("dep_date", ""), search_year)
        seg_arr_date = _sq_date_to_iso(seg_data.get("arr_date", ""), search_year)
        seg_arr_time = _convert_time_12h(seg_data.get("arr_time", ""))
        # +N day on segment
        if seg_dep_date and seg_arr_date and seg_dep_date != seg_arr_date:
            try:
                sd = date.fromisoformat(seg_dep_date)
                sa = date.fromisoformat(seg_arr_date)
                diff = (sa - sd).days
                if diff > 0:
                    seg_arr_time = f"{seg_arr_time}+{diff}"
            except ValueError:
                pass

        segments.append(Segment(
            flight_number=seg_data.get("flight_number", "").replace(" ", ""),
            airline_name="Singapore Airlines",
            aircraft=seg_data.get("aircraft", ""),
            departure_airport=seg_data.get("dep_airport", ""),
            arrival_airport=seg_data.get("arr_airport", ""),
            departure_time=_convert_time_12h(seg_data.get("dep_time", "")),
            arrival_time=seg_arr_time,
            duration="",  # SQ doesn't show per-segment duration in DOM
            departure_date=seg_dep_date,
            arrival_date=seg_arr_date,
            has_wifi=True,  # SQ business generally has wifi
            has_power=True,
            seat_type=seg_data.get("cabin", "Business"),
        ))

    # Price
    price_text = data.get("price_text", "")
    price_usd, price_raw = _parse_price_to_usd(price_text)
    price_unavailable = price_usd is None

    ref = ",".join(flight_numbers)
    airline = "Singapore Airlines"

    return Flight(
        airline=airline,
        price=price_usd,
        price_currency="$",
        price_raw=price_raw,
        departure_time=dep_time,
        arrival_time=arr_time,
        departure_airport=dep_airport,
        arrival_airport=arr_airport,
        duration=duration,
        stops=stops,
        layovers=layovers,
        ref=ref,
        price_unavailable=price_unavailable,
        flight_numbers=flight_numbers,
        aircraft_types=aircraft_types,
        departure_date=dep_date_iso or search_date.isoformat(),
        is_round_trip=is_round_trip,
        segments=segments,
    )


# ---------------------------------------------------------------------------
# Calendar date selection helpers
# ---------------------------------------------------------------------------

def _get_calendar_month_offset(target_date: date) -> int:
    """Calculate how many times to click 'next month' from the current month."""
    today = date.today()
    current_month = today.year * 12 + today.month - 1
    target_month = target_date.year * 12 + target_date.month - 1
    # Calendar starts showing current month on the left
    return max(0, target_month - current_month)


def _click_calendar_date_js(session: str, target_date: date, is_departure: bool) -> bool:
    """Click a specific date in the SQ calendar using JavaScript.

    The calendar has UL.calendar_days rows (one per week), each with LI children.
    We need to find the right month panel and click the right day.
    """
    target_day = target_date.day
    target_month = target_date.month

    # The calendar shows 2 months side by side. We need to figure out which panel
    # has our target month and click the right day within it.
    js = f"""
    (function() {{
        var root = document.querySelector('.calendar-root');
        if (!root) return 'no-calendar';
        var uls = root.querySelectorAll('ul.calendar_days');
        // Each UL is a week row. LIs within are day cells.
        // We need to find the LI whose text starts with our target day number
        // and is in the right month column.

        // The calendar has two month panels. Find the select/header elements.
        var monthSelects = root.querySelectorAll('select, .month-title, [class*=month-year]');
        // Alternative: just scan all LIs and click the one matching our day
        // We know the month from context (which panel it's in)

        // Simple approach: iterate all LIs, find those starting with target day
        var allLis = root.querySelectorAll('li:not(.calendar_days--disabled)');
        var candidates = [];
        for (var i = 0; i < allLis.length; i++) {{
            var text = allLis[i].textContent.trim();
            if (text.match(/^{target_day}(?:\\D|$)/)) {{
                candidates.push({{el: allLis[i], idx: i, text: text.substring(0, 30)}});
            }}
        }}

        // If target month is in the second panel, take the last match
        // (first panel = current/earlier month, second panel = later month)
        if (candidates.length === 0) return 'no-match';

        // For departure date, click the first candidate that isn't already selected
        // For return date (second click), we need the candidate in the right month
        var target = candidates[candidates.length > 1 && !{str(is_departure).lower()} ? candidates.length - 1 : 0];
        target.el.click();
        return 'clicked-day-{target_day}';
    }})()
    """
    result = _ab_eval(session, js)
    return "clicked" in result


# ---------------------------------------------------------------------------
# Core browser search flow
# ---------------------------------------------------------------------------

def _search_sq_browser(
    session: str,
    origin: str,
    dest: str,
    depart_date: date,
    return_date: date | None = None,
    cabin: str = "business",
) -> list[Flight]:
    """Execute a full SQ search via browser automation. Returns parsed flights."""

    origin_city = AIRPORT_CITY_NAMES.get(origin.upper(), origin)
    dest_city = AIRPORT_CITY_NAMES.get(dest.upper(), dest)
    cabin_label = {"business": "Business", "first": "First/Suites",
                   "premium_economy": "Premium Economy", "economy": "Economy"}.get(cabin, "Business")

    # Step 1: Open SQ homepage with Akamai warmup
    print(f"  [SQ] Opening singaporeair.com...", file=sys.stderr)
    if not _ab_open(session, "https://www.singaporeair.com/en_US/"):
        return []

    # Reload to let Akamai cookies settle (first load often triggers challenge)
    _ab(session, "reload", timeout=15)
    _ab_wait(session, 8000)

    # Check if we got blocked
    blocked_check = _ab_eval(session, "document.title")
    if "Maintenance" in blocked_check or "error" in blocked_check.lower():
        _ab_wait(session, 5000)
        _ab(session, "reload", timeout=15)
        _ab_wait(session, 8000)
        blocked_check = _ab_eval(session, "document.title")
        if "Maintenance" in blocked_check:
            print(f"  [SQ] Blocked by Akamai/maintenance page", file=sys.stderr)
            return []

    # Step 2: Accept cookie banner if present
    snap = _ab_snapshot(session)
    if "Accept" in snap:
        for line in snap.splitlines():
            if 'button "Accept"' in line:
                m = re.search(r'\[ref=(e\d+)\]', line)
                if m:
                    _ab_click_ref(session, f"@{m.group(1)}")
                    _ab_wait(session, 1000)
                break

    # Step 3: Fill origin
    print(f"  [SQ] Setting origin: {origin_city} ({origin})", file=sys.stderr)
    snap = _ab_snapshot(session)
    from_ref = _find_ref(snap, 'textbox "From"')
    if not from_ref:
        return []
    _ab_click_ref(session, f"@{from_ref}")
    _ab_wait(session, 500)
    _ab_fill(session, f"@{from_ref}", origin_city)
    _ab_wait(session, 3000)
    _click_autocomplete_suggestion(session, origin, origin_city)
    _ab_wait(session, 2000)

    # Step 4: Fill destination
    print(f"  [SQ] Setting destination: {dest_city} ({dest})", file=sys.stderr)
    snap = _ab_snapshot(session)
    to_ref = _find_ref(snap, 'textbox "To"')
    if not to_ref:
        return []
    _ab_click_ref(session, f"@{to_ref}")
    _ab_wait(session, 500)
    _ab_fill(session, f"@{to_ref}", dest_city)
    _ab_wait(session, 3000)
    _click_autocomplete_suggestion(session, dest, dest_city)
    _ab_wait(session, 2000)

    # Step 5: Set departure date
    print(f"  [SQ] Setting departure: {depart_date}", file=sys.stderr)
    snap = _ab_snapshot(session)
    depart_ref = _find_ref(snap, 'textbox "Depart')
    if depart_ref:
        _ab_click_ref(session, f"@{depart_ref}")
        _ab_wait(session, 2000)

    # Navigate calendar to target month if needed
    _navigate_calendar_to_month(session, depart_date)
    _ab_wait(session, 500)

    # Click the departure day
    _click_calendar_date_js(session, depart_date, is_departure=True)
    _ab_wait(session, 1000)

    # Step 6: Set return date (if RT) or check One-way
    if return_date:
        print(f"  [SQ] Setting return: {return_date}", file=sys.stderr)
        _navigate_calendar_to_month(session, return_date)
        _ab_wait(session, 500)
        _click_calendar_date_js(session, return_date, is_departure=False)
        _ab_wait(session, 1000)
    else:
        # One-way: check the One-way checkbox
        print(f"  [SQ] Setting one-way", file=sys.stderr)
        snap = _ab_snapshot(session)
        ow_ref = _find_ref(snap, 'checkbox "One-way"')
        if ow_ref:
            _ab_click_ref(session, f"@{ow_ref}")
            _ab_wait(session, 1000)

    # Click Done to close calendar
    snap = _ab_snapshot(session)
    done_ref = _find_ref(snap, 'button "Done"')
    if done_ref:
        _ab_click_ref(session, f"@{done_ref}")
        _ab_wait(session, 1000)

    # Step 7: Set cabin class
    print(f"  [SQ] Setting cabin: {cabin_label}", file=sys.stderr)
    snap = _ab_snapshot(session)
    class_ref = _find_ref(snap, 'textbox "Class"')
    if class_ref:
        _ab_click_ref(session, f"@{class_ref}")
        _ab_wait(session, 1000)
        _ab_find_click(session, cabin_label)
        _ab_wait(session, 1000)

    # Step 8: Click Search
    print(f"  [SQ] Searching...", file=sys.stderr)
    snap = _ab_snapshot(session)
    search_ref = _find_ref(snap, 'button "Search"')
    if search_ref:
        _ab_click_ref(session, f"@{search_ref}")
    _ab_wait(session, 3000)

    # Dismiss "Try new booking experience" modal if it appears
    _ab_find_click(session, "Not now")
    _ab_wait(session, 3000)

    # Wait for results to load — SQ results page takes 15-20s
    _ab(session, "wait", "--load", "networkidle", timeout=45)
    _ab_wait(session, 15000)

    # Step 9: Click "More details" on all flights to expand segment info
    print(f"  [SQ] Expanding flight details...", file=sys.stderr)
    _ab_eval(session, """
        document.querySelectorAll('a[class*=details], [class*=more-details]').forEach(function(el) {
            if (el.textContent.toLowerCase().includes('more details')) el.click();
        });
    """)
    _ab_wait(session, 2000)

    # Step 10: Extract flight data via JS
    print(f"  [SQ] Extracting flight data...", file=sys.stderr)
    rc, raw_out, _ = _ab(session, "eval", EXTRACT_FLIGHTS_JS, timeout=20)
    if rc != 0 or not raw_out.strip():
        print(f"  [SQ] No flight data extracted", file=sys.stderr)
        return []

    # agent-browser wraps eval output in quotes — strip and unescape
    raw_json = raw_out.strip()
    if raw_json.startswith('"') and raw_json.endswith('"'):
        raw_json = raw_json[1:-1]
    raw_json = raw_json.replace('\\"', '"').replace('\\n', '\n')

    try:
        extracted = json.loads(raw_json)
    except json.JSONDecodeError as e:
        print(f"  [SQ] Failed to parse extracted data: {e}", file=sys.stderr)
        return []

    # Parse into Flight objects
    is_rt = return_date is not None
    flights = []
    for data in extracted:
        flight = _parse_extracted_flight(data, depart_date, is_round_trip=is_rt)
        if flight:
            flights.append(flight)

    print(f"  [SQ] Found {len(flights)} flights", file=sys.stderr)
    return flights


def _find_ref(snapshot: str, label_pattern: str) -> str | None:
    """Find a ref ID from snapshot text by matching a label pattern."""
    for line in snapshot.splitlines():
        if label_pattern in line:
            m = re.search(r'\[ref=(e\d+)\]', line)
            if m:
                return m.group(1)
    return None


def _navigate_calendar_to_month(session: str, target_date: date) -> None:
    """Click the calendar forward arrow until the target month is visible."""
    for _ in range(12):  # max 12 months forward
        # Check which months are currently visible
        visible_months = _ab_eval(session, """
            var selects = document.querySelectorAll('.calendar-root select');
            var result = [];
            selects.forEach(function(s) { result.push(s.value || s.textContent.trim()); });
            if (result.length === 0) {
                var headers = document.querySelectorAll('.calendar-root [class*=month]');
                headers.forEach(function(h) {
                    var text = h.textContent.trim();
                    if (text.match(/\\w+ \\d{4}/)) result.push(text);
                });
            }
            result.join('|');
        """)

        target_month_str = target_date.strftime("%B %Y")  # e.g., "April 2026"
        if target_month_str.lower() in visible_months.lower():
            return  # Target month is visible

        # Click next month arrow
        _ab_eval(session, """
            var nextBtn = document.querySelector('.calendar-root [class*=next], .calendar-root .icon-right, .calendar-root button[class*=arrow]');
            if (nextBtn) nextBtn.click();
            else {
                var btns = document.querySelectorAll('.calendar-root button');
                for (var i = 0; i < btns.length; i++) {
                    if (btns[i].textContent.trim() === '>' || btns[i].getAttribute('aria-label') === 'Next') {
                        btns[i].click();
                        break;
                    }
                }
            }
        """)
        _ab_wait(session, 1000)


# ---------------------------------------------------------------------------
# Public API — search with retry and caching
# ---------------------------------------------------------------------------

def search_sq(
    origin: str,
    dest: str,
    depart_date: date,
    max_stops: int | None = None,
    cabin: str = "business",
    use_cache: bool = True,
) -> list[Flight]:
    """Search one-way SQ flights via browser with retry and caching."""
    cache_k = _cache_key(origin, dest, depart_date, cabin, max_stops)
    if use_cache:
        cached = _read_cache(cache_k)
        if cached is not None:
            print(f"  [SQ cache hit: {origin}->{dest} {depart_date}]", file=sys.stderr)
            return _flights_from_cache(cached)

    session = f"sq_{origin}_{dest}_{depart_date.isoformat().replace('-', '')}"

    last_err = None
    for attempt in range(SQ_MAX_RETRIES):
        try:
            flights = _search_sq_browser(session, origin, dest, depart_date, cabin=cabin)

            # Filter to outbound only (origin matches)
            outbound = [f for f in flights if f.departure_airport == origin.upper()]

            if use_cache and outbound:
                _write_cache(cache_k, outbound)

            return outbound

        except Exception as e:
            last_err = e
            print(f"  [SQ attempt {attempt + 1}/{SQ_MAX_RETRIES} failed: {e}]", file=sys.stderr)
            if attempt < SQ_MAX_RETRIES - 1:
                time.sleep(SQ_RETRY_DELAY)
        finally:
            _ab_close(session)
            time.sleep(1)

    print(f"SQ search failed after {SQ_MAX_RETRIES} attempts: {last_err}", file=sys.stderr)
    return []


def search_sq_roundtrip(
    origin: str,
    dest: str,
    depart_date: date,
    return_date: date,
    max_stops: int | None = None,
    cabin: str = "business",
    use_cache: bool = True,
) -> list[Flight]:
    """Search round-trip SQ flights via browser."""
    cache_k = _cache_key(origin, dest, depart_date, cabin, max_stops, return_date=return_date)
    if use_cache:
        cached = _read_cache(cache_k)
        if cached is not None:
            print(f"  [SQ cache hit: {origin}->{dest} RT {depart_date}/{return_date}]", file=sys.stderr)
            return _flights_from_cache(cached)

    session = f"sq_rt_{origin}_{dest}_{depart_date.isoformat().replace('-', '')}"

    last_err = None
    for attempt in range(SQ_MAX_RETRIES):
        try:
            flights = _search_sq_browser(session, origin, dest, depart_date,
                                          return_date=return_date, cabin=cabin)

            if use_cache and flights:
                _write_cache(cache_k, flights)

            return flights

        except Exception as e:
            last_err = e
            print(f"  [SQ RT attempt {attempt + 1}/{SQ_MAX_RETRIES} failed: {e}]", file=sys.stderr)
            if attempt < SQ_MAX_RETRIES - 1:
                time.sleep(SQ_RETRY_DELAY)
        finally:
            _ab_close(session)
            time.sleep(1)

    print(f"SQ RT search failed after {SQ_MAX_RETRIES} attempts: {last_err}", file=sys.stderr)
    return []


# ---------------------------------------------------------------------------
# Parallel multi-date search
# ---------------------------------------------------------------------------

def search_sq_parallel(
    origin: str,
    dest: str,
    dates: list[date],
    max_stops: int | None = None,
    cabin: str = "business",
    use_cache: bool = True,
) -> dict[date, list[Flight]]:
    """Search multiple dates sequentially (browser sessions are heavy)."""
    results: dict[date, list[Flight]] = {}

    # Sequential for browser-based searches — each needs a full browser
    for d in dates:
        flights = search_sq(origin, dest, d, max_stops=max_stops,
                            cabin=cabin, use_cache=use_cache)
        results[d] = flights
        time.sleep(SQ_INTER_SEARCH_DELAY)

    return results


def search_sq_roundtrip_parallel(
    origin: str,
    dest: str,
    date_pairs: list[tuple[date, date]],
    max_stops: int | None = None,
    cabin: str = "business",
    use_cache: bool = True,
) -> dict[date, list[Flight]]:
    """Search multiple RT date pairs sequentially."""
    results: dict[date, list[Flight]] = {}

    for dep, ret in date_pairs:
        flights = search_sq_roundtrip(origin, dest, dep, ret,
                                       max_stops=max_stops, cabin=cabin, use_cache=use_cache)
        results[dep] = flights
        time.sleep(SQ_INTER_SEARCH_DELAY)

    return results
