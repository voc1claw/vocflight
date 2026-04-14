"""Parse agent-browser snapshot text into structured flight data."""

import re
from dataclasses import dataclass, field


@dataclass
class Layover:
    duration: str  # e.g., "3 hr 30 min"
    airport: str   # e.g., "Narita International Airport"
    city: str      # e.g., "Tokyo"
    code: str      # e.g., "NRT" (extracted or mapped)


@dataclass
class Segment:
    """A single flight segment (one takeoff + landing)."""
    flight_number: str      # e.g., "AF217"
    airline_name: str       # e.g., "Air France"
    aircraft: str           # e.g., "Boeing 777"
    departure_airport: str  # IATA code e.g., "BOM"
    arrival_airport: str    # IATA code e.g., "CDG"
    departure_time: str     # e.g., "1:20 AM"
    arrival_time: str       # e.g., "7:25 AM"
    duration: str           # e.g., "9 hr 35 min"
    departure_date: str     # YYYY-MM-DD
    arrival_date: str       # YYYY-MM-DD
    has_wifi: bool = False
    has_power: bool = False
    seat_type: str = ""     # e.g., "Lie-flat seat"


@dataclass
class Flight:
    airline: str
    price: float | None
    price_currency: str
    price_raw: str  # original text e.g., "6956 US dollars"
    departure_time: str
    arrival_time: str
    departure_airport: str
    arrival_airport: str
    duration: str
    stops: int
    layovers: list[Layover] = field(default_factory=list)
    ref: str = ""  # snapshot ref e.g., "e37"
    price_unavailable: bool = False
    labels: list[str] = field(default_factory=list)  # "Best", "Cheapest", etc.
    flight_numbers: list[str] = field(default_factory=list)  # e.g., ["CX750", "CX883"]
    aircraft_types: list[str] = field(default_factory=list)  # e.g., ["Boeing 777-300ER"]
    departure_date: str = ""  # YYYY-MM-DD
    is_round_trip: bool = False  # True = price is RT total
    segments: list[Segment] = field(default_factory=list)  # per-segment details


# Patterns for extracting data from snapshot link text
_PRICE_PATTERN = re.compile(
    r"From\s+([\d,]+)\s+(.+?)\s*(?:round trip|one way|total|\.)",
    re.IGNORECASE,
)
_PRICE_UNAVAIL = re.compile(r"price\s+unavailable", re.IGNORECASE)
_STOPS_PATTERN = re.compile(
    r"(Nonstop|(\d+)\s+stop[s]?)\s+flight\s+with\s+(.+?)(?:\.\s*Leaves|\.\s*Departs)",
    re.IGNORECASE,
)
_DEPART_PATTERN = re.compile(
    r"Leaves\s+(.+?)\s+at\s+(\d{1,2}:\d{2}\s*[AP]M)\s+on\s+\w+,\s+(\w+\s+\d{1,2}(?:,?\s*\d{4})?)",
    re.IGNORECASE,
)
_ARRIVE_PATTERN = re.compile(
    r"arrives\s+at\s+(.+?)\s+at\s+(\d{1,2}:\d{2}\s*[AP]M)\s+on\s+\w+,\s+(\w+\s+\d{1,2}(?:,?\s*\d{4})?)",
    re.IGNORECASE,
)
_DURATION_PATTERN = re.compile(
    r"Total\s+duration\s+(\d+\s*hr?\s*(?:\d+\s*min)?)",
    re.IGNORECASE,
)
_LAYOVER_PATTERN = re.compile(
    r"Layover\s*\(\d+\s+of\s+\d+\)\s+is\s+a\s+(.+?)\s+layover\s+at\s+(.+?)\s+in\s+(.+?)(?:\.|$)",
    re.IGNORECASE,
)
_REF_PATTERN = re.compile(r"\[ref=(e\d+)\]")

# Currency word → symbol mapping
CURRENCY_MAP = {
    "us dollar": "$",
    "us dollars": "$",
    "dollar": "$",
    "dollars": "$",
    "thai baht": "THB",
    "baht": "THB",
    "euro": "EUR",
    "euros": "EUR",
    "pound": "GBP",
    "pounds": "GBP",
    "indian rupee": "INR",
    "indian rupees": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "japanese yen": "JPY",
    "yen": "JPY",
}

# Common airport name → IATA code mapping
AIRPORT_CODES = {
    "chhatrapati shivaji maharaj international airport mumbai": "BOM",
    "chhatrapati shivaji maharaj international airport": "BOM",
    "los angeles international airport": "LAX",
    "narita international airport": "NRT",
    "haneda airport": "HND",
    "tokyo haneda airport": "HND",
    "hong kong international airport": "HKG",
    "singapore changi airport": "SIN",
    "changi airport": "SIN",
    "suvarnabhumi airport": "BKK",
    "dubai international airport": "DXB",
    "istanbul airport": "IST",
    "charles de gaulle airport": "CDG",
    "amsterdam airport schiphol": "AMS",
    "amsterdam schiphol airport": "AMS",
    "frankfurt airport": "FRA",
    "frankfurt am main airport": "FRA",
    "munich airport": "MUC",
    "zurich airport": "ZRH",
    "heathrow airport": "LHR",
    "london heathrow airport": "LHR",
    "john f. kennedy international airport": "JFK",
    "jomo kenyatta international airport": "NBO",
    "addis ababa bole international airport": "ADD",
    "o.r. tambo international airport": "JNB",
    "or tambo international airport": "JNB",
    "queen alia international airport": "AMM",
    "hamad international airport": "DOH",
    "abu dhabi international airport": "AUH",
    "incheon international airport": "ICN",
    "taiwan taoyuan international airport": "TPE",
    "kigali international airport": "KGL",
    "julius nyerere international airport": "DAR",
    "cairo international airport": "CAI",
    "entebbe international airport": "EBB",
}


def _resolve_airport_code(name: str) -> str:
    """Try to extract IATA code from airport name."""
    name_lower = name.lower().strip()
    for full_name, code in AIRPORT_CODES.items():
        if full_name in name_lower or name_lower in full_name:
            return code
    # Try to find a 3-letter code in parentheses
    m = re.search(r"\(([A-Z]{3})\)", name)
    if m:
        return m.group(1)
    return name[:30]  # fallback: truncated name


def _resolve_currency(currency_text: str) -> str:
    """Convert currency word to symbol/code."""
    text_lower = currency_text.lower().strip()
    for word, symbol in CURRENCY_MAP.items():
        if word in text_lower:
            return symbol
    return currency_text.upper()


def _parse_duration_to_minutes(dur_str: str) -> float:
    """Parse '21 hr 35 min' or '8 hr' to total minutes."""
    hours = 0
    minutes = 0
    h_match = re.search(r"(\d+)\s*hr?", dur_str)
    m_match = re.search(r"(\d+)\s*min", dur_str)
    if h_match:
        hours = int(h_match.group(1))
    if m_match:
        minutes = int(m_match.group(1))
    return hours * 60 + minutes


def parse_layover_duration_hours(dur_str: str) -> float:
    """Parse layover duration string to hours. E.g., '3 hr 30 min' -> 3.5"""
    mins = _parse_duration_to_minutes(dur_str)
    return mins / 60


def parse_snapshot(snapshot_text: str) -> list[Flight]:
    """Parse a Google Flights snapshot into a list of Flight objects."""
    flights = []

    # Find all link elements that look like flight results
    # Each flight is a link with price + airline + times info
    for line in snapshot_text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Must be a link element with flight-like content
        if 'link "' not in line.lower() and 'link "' not in line:
            continue

        # Extract the link text between quotes
        quote_match = re.search(r'link\s+"(.+?)"', line, re.IGNORECASE)
        if not quote_match:
            continue

        text = quote_match.group(1)

        # Must contain flight-related content
        if "Select flight" not in text and "select flight" not in text.lower():
            continue

        # Extract ref
        ref = ""
        ref_match = _REF_PATTERN.search(line)
        if ref_match:
            ref = ref_match.group(1)

        # Extract price
        price = None
        price_currency = ""
        price_raw = ""
        price_unavailable = False

        price_match = _PRICE_PATTERN.search(text)
        if price_match:
            price_raw = price_match.group(0)
            price_str = price_match.group(1).replace(",", "")
            price = float(price_str)
            price_currency = _resolve_currency(price_match.group(2))
        elif _PRICE_UNAVAIL.search(text):
            price_unavailable = True
            price_raw = "Price unavailable"

        # Extract stops and airlines
        stops = 0
        airline = ""
        stops_match = _STOPS_PATTERN.search(text)
        if stops_match:
            if stops_match.group(1).lower().startswith("nonstop"):
                stops = 0
            else:
                stops = int(stops_match.group(2))
            airline = stops_match.group(3).strip()
            # Clean up airline: "ANA and United" -> "ANA + United"
            airline = re.sub(r"\s+and\s+", " + ", airline)

        # Extract departure
        dep_time = ""
        dep_airport = ""
        dep_match = _DEPART_PATTERN.search(text)
        if dep_match:
            dep_airport = dep_match.group(1)
            dep_time = dep_match.group(2)

        # Extract arrival
        arr_time = ""
        arr_airport = ""
        arr_match = _ARRIVE_PATTERN.search(text)
        if arr_match:
            arr_airport = arr_match.group(1)
            arr_time = arr_match.group(2)

        # Extract duration
        duration = ""
        dur_match = _DURATION_PATTERN.search(text)
        if dur_match:
            duration = dur_match.group(1).strip()

        # Extract layovers
        layovers = []
        for lay_match in _LAYOVER_PATTERN.finditer(text):
            lay_dur = lay_match.group(1).strip()
            lay_airport = lay_match.group(2).strip()
            lay_city = lay_match.group(3).strip()
            lay_code = _resolve_airport_code(lay_airport)
            layovers.append(Layover(
                duration=lay_dur,
                airport=lay_airport,
                city=lay_city,
                code=lay_code,
            ))

        if airline or price is not None or price_unavailable:
            flights.append(Flight(
                airline=airline,
                price=price,
                price_currency=price_currency,
                price_raw=price_raw,
                departure_time=dep_time,
                arrival_time=arr_time,
                departure_airport=_resolve_airport_code(dep_airport) if dep_airport else "",
                arrival_airport=_resolve_airport_code(arr_airport) if arr_airport else "",
                duration=duration,
                stops=stops,
                layovers=layovers,
                ref=ref,
                price_unavailable=price_unavailable,
            ))

    return flights
