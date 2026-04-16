"""VOCFLIGHT - AI-powered flight search chat interface."""

import io
import json
import os
import re
import sys
from functools import wraps
from datetime import date, timedelta

# Fix Windows UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Ensure both the project root and flight/ are importable in this Python setup.
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "flight"))


def load_env_file():
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


load_env_file()

from flask import Flask, render_template, request, jsonify, redirect, session, url_for
import requests as http_requests

from flight_search.swoop_backend import search_swoop_parallel, search_swoop_roundtrip_parallel
from flight_search.filters import apply_all_filters
from flight_search.config import BANNED_AIRLINES
from supabase_store import ALL_MODELS, DEFAULT_ENABLED_MODEL_IDS, SupabaseStore, serialize_bootstrap_user

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "vocflight-dev-secret-change-me")
store = SupabaseStore()

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

FLIGHT_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_flights",
        "description": "Search for flights between airports on specific dates. Use this when the user wants to find, compare, or check flight prices/availability.",
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {
                    "type": "string",
                    "description": "Origin IATA airport code (3 letters). Convert city names: NYC->JFK, London->LHR, Mumbai->BOM, LA->LAX, Dubai->DXB, Tokyo->NRT, Paris->CDG, Singapore->SIN"
                },
                "destination": {
                    "type": "string",
                    "description": "Destination IATA airport code (3 letters)"
                },
                "dates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Departure dates as YYYY-MM-DD strings. For flexible dates, include multiple dates."
                },
                "return_after_days": {
                    "type": "integer",
                    "description": "Return N days after each departure date. Creates a round-trip search. E.g., 30 means return 30 days after departure."
                },
                "return_date": {
                    "type": "string",
                    "description": "Exact return date YYYY-MM-DD for round-trip. Use this OR return_after_days, not both."
                },
                "one_way": {
                    "type": "boolean",
                    "description": "If true, search one-way only. Default false (search both outbound and return)."
                },
                "max_stops": {
                    "type": "integer",
                    "description": "Maximum stops allowed. Default 1. Use 0 for nonstop only."
                },
                "cabin": {
                    "type": "string",
                    "enum": ["economy", "business", "first"],
                    "description": "Cabin class. Default is business."
                },
                "exclude_routing": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "IATA codes to exclude from routing (e.g., ['DXB', 'AUH', 'DOH'] to avoid Middle East routing)"
                }
            },
            "required": ["origin", "destination", "dates"]
        }
    }
}


def get_system_prompt():
    today = date.today().isoformat()
    banned = ", ".join(sorted(BANNED_AIRLINES))
    return f"""You are VOCFlight, an expert AI flight search assistant and travel advisor. Today's date is {today}.

You search real Google Flights data and provide expert advice on flight scheduling, routing, layovers, airlines, and travel planning.

=====================
MANDATORY: COLLECT BEFORE SEARCHING
=====================
You MUST have ALL FOUR pieces of information before calling search_flights. If ANY is missing, ASK the user — do NOT guess or assume:

1. LOCATION — Both origin AND destination
   - If user says "I want to fly to Tokyo" but no origin, ask: "Where will you be departing from?"
   - If user says "flights from NYC" but no destination, ask: "Where would you like to fly to?"

2. DATE — At least a departure date or date range
   - If no date at all, ask: "What dates are you looking at? A specific date, or a flexible range like 'early May' or 'next week'?"
   - If vague like "sometime soon", suggest: "How about I search the next 7 days starting tomorrow?"

3. CABIN CLASS — Business Class or Economy Class
   - If the user does NOT mention "business", "economy", "first class", or any cabin class, you MUST ask:
     "Would you like to fly Business Class or Economy Class?"
   - Do NOT default silently. Always confirm cabin preference.
   - Only if user explicitly says "business" or "economy" or "first" can you skip asking.

4. TRIP TYPE — Round-trip or One-way
   - If the user does NOT specify "round trip", "return", "one way", or "one-way", you MUST ask:
     "Is this a round-trip or one-way flight?"
   - If round-trip, also ask for the return date if not provided: "When would you like to return?"
   - Only if user explicitly mentions trip type can you skip asking.

ASK ALL MISSING ITEMS IN A SINGLE MESSAGE. For example, if location and date are provided but cabin and trip type are missing, ask both at once:
"Just a couple more details:
- Would you like Business Class or Economy Class?
- Is this a round-trip or one-way?"

If the user provides ALL four in one message (e.g., "Round-trip business class flights from NYC to London, May 15 returning May 22"), search immediately.

=====================
FLIGHT & SCHEDULE EXPERTISE
=====================
You are an expert flight advisor. Use this knowledge when helping users:

ROUTING ADVICE:
- Trans-Pacific (US West Coast <-> Asia): Best via NRT/HND (Tokyo), HKG (Hong Kong), SIN (Singapore), ICN (Seoul). Direct flights on JAL, ANA, Cathay, Singapore Airlines.
- Trans-Atlantic (US East Coast <-> Europe): Nonstops available JFK/EWR to LHR, CDG, FRA, AMS. Best carriers: British Airways, Air France, Lufthansa, Delta, United.
- US <-> India: Best via Middle East (DXB, DOH) on Emirates/Qatar, or via Europe (LHR, FRA, CDG) on BA/Lufthansa/Air France, or via Asia (HKG, SIN) on Cathay/SQ.
- Africa routes: Emirates via DXB is often best for East/Southern Africa. Ethiopian via ADD for budget. South African Airways for JNB direct.
- Wrong-way routing (e.g., BOM->LAX via Asia eastward) can sometimes be cheaper — flag these as "wrong way" but include them if the price is compelling.

SCHEDULING ADVICE:
- Red-eye flights: Overnight departures that arrive next morning. Good for maximizing time, but tiring on long-haul.
- Connection times: Minimum 1.5-2hr for international connections. Under 1hr is risky. Over 6hr is a long layover — flag it.
- Day-of-week pricing: Tuesdays and Wednesdays are typically cheapest. Fridays and Sundays most expensive.
- Advance booking: Business class is cheapest 2-3 months out. Last-minute biz fares are very expensive.
- Seasonal patterns: Peak season (Jun-Aug, Dec) = higher prices. Shoulder season (Apr-May, Sep-Oct) = best deals.
- Arrival timing: For business travelers, arriving early morning is ideal. For leisure, afternoon is more relaxing.

AIRLINE ADVICE:
- Best business class products: Singapore Airlines, Qatar Airways, Emirates, ANA, Cathay Pacific, Japan Airlines.
- Best value business: Turkish Airlines (IST hub), Air France (CDG), KLM (AMS).
- Budget-premium carriers: EVA Air, Korean Air — excellent business class at lower prices.
- Avoid for business class: Budget carriers (Ryanair, Spirit, etc.) don't have real business class.

LAYOVER ADVICE:
- Good layover airports: SIN (Changi — best airport in the world, lounges, gardens), DOH (Hamad — excellent), HKG, NRT, ICN (great lounges and transit facilities).
- Difficult layover airports: Some airports require visa for transit, landside transfers between terminals. Flag these.
- Long layovers (4-8hr): Can be pleasant at good airports. Suggest lounge access.
- Very long layovers (8hr+): Consider if an overnight hotel or exploring the city is an option.

=====================
SEARCH RULES
=====================
- Default cabin: BUSINESS (unless user explicitly asks for economy or first)
- Banned airlines (never show): {banned}
- IATA codes: NYC->JFK, London->LHR, Mumbai/Bombay->BOM, LA/Los Angeles->LAX, Dubai->DXB, Tokyo->NRT/HND, Paris->CDG, Singapore->SIN, Hong Kong->HKG, Delhi->DEL, Chicago->ORD, San Francisco->SFO, Bangkok->BKK, Johannesburg->JNB, Sydney->SYD, Melbourne->MEL, Toronto->YYZ, Doha->DOH
- Even if a trip is round-trip, always check two separate one-way tickets
- For round-trip queries, set return_after_days or return_date so BOTH directions are searched

DATE RANGE EXPANSION:
- "next week": 7 dates starting next Monday
- "after DATE": DATE+1 through DATE+7
- "early MONTH": 1st-10th, "mid MONTH": 11th-20th, "late MONTH": 21st-last
- "anytime in MONTH": representative dates (1st, 5th, 10th, 15th, 20th, 25th)
- Always convert relative dates to specific YYYY-MM-DD based on today ({today})

=====================
RESPONSE STYLE
=====================
- Be concise, knowledgeable, and friendly
- When advising (no search needed): Share specific routing tips, airline recommendations, timing advice
- When searching: Give a brief intro, then the flight cards handle the details
- Proactively mention: best time to book, alternate airports, day-of-week savings, layover quality
- If no results found: suggest alternative dates, nearby airports, or different routing
- After results: The system will automatically show the best deal with booking links

=====================
TOOL USAGE
=====================
IMPORTANT: When you need to search flights, you MUST call the search_flights function/tool. If you cannot use tools, output your search request as a JSON block like this:
```json
{{"action": "search_flights", "origin": "BOM", "destination": "LAX", "dates": ["2026-04-15"], "max_stops": 1, "cabin": "business"}}
```

Do NOT search without both LOCATION and DATE confirmed. Ask first if missing."""


def call_openrouter(messages, model):
    """Call OpenRouter API with tool support."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:5000",
        "X-Title": "VOCFlight",
    }

    payload = {
        "model": model,
        "messages": messages,
        "tools": [FLIGHT_SEARCH_TOOL],
        "temperature": 0.7,
    }

    try:
        resp = http_requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json()
    except http_requests.exceptions.HTTPError as e:
        # If tool calling not supported, retry without tools
        if resp.status_code == 400 or resp.status_code == 422:
            payload.pop("tools", None)
            try:
                resp2 = http_requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
                resp2.raise_for_status()
                return resp2.json()
            except Exception as e2:
                return {"error": str(e2)}
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def extract_tool_call_from_text(text):
    """Fallback: extract flight search JSON from AI text when tools aren't supported."""
    patterns = [
        r'```json\s*(\{[^`]*?"action"\s*:\s*"search_flights"[^`]*?\})\s*```',
        r'(\{[^{}]*?"action"\s*:\s*"search_flights"[^{}]*?\})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                return {
                    "origin": data.get("origin", ""),
                    "destination": data.get("destination", ""),
                    "dates": data.get("dates", []),
                    "max_stops": data.get("max_stops", 1),
                    "cabin": data.get("cabin", "business"),
                }
            except json.JSONDecodeError:
                pass
    return None


CITY_TO_IATA = {
    "NYC": "JFK", "NEW YORK": "JFK", "NEWYORK": "JFK",
    "LA": "LAX", "LOS ANGELES": "LAX",
    "SF": "SFO", "SAN FRANCISCO": "SFO",
    "LONDON": "LHR", "MUMBAI": "BOM", "BOMBAY": "BOM",
    "DUBAI": "DXB", "TOKYO": "NRT", "PARIS": "CDG",
    "SINGAPORE": "SIN", "HONG KONG": "HKG", "HONGKONG": "HKG",
    "DELHI": "DEL", "NEW DELHI": "DEL", "CHICAGO": "ORD",
    "BANGKOK": "BKK", "JOHANNESBURG": "JNB", "JOBURG": "JNB",
    "SYDNEY": "SYD", "MELBOURNE": "MEL", "TORONTO": "YYZ",
    "DOHA": "DOH", "SEOUL": "ICN", "BEIJING": "PEK",
    "SHANGHAI": "PVG", "ISTANBUL": "IST", "AMSTERDAM": "AMS",
    "FRANKFURT": "FRA", "ZURICH": "ZRH", "MUNICH": "MUC",
    "ROME": "FCO", "MILAN": "MXP", "MADRID": "MAD",
    "BARCELONA": "BCN", "LISBON": "LIS", "CAIRO": "CAI",
    "NAIROBI": "NBO", "CAPE TOWN": "CPT", "MANILA": "MNL",
    "KUALA LUMPUR": "KUL", "JAKARTA": "CGK", "TAIPEI": "TPE",
    "OSAKA": "KIX", "WASHINGTON": "IAD", "DC": "IAD",
    "BOSTON": "BOS", "SEATTLE": "SEA", "DENVER": "DEN",
    "ATLANTA": "ATL", "MIAMI": "MIA", "DALLAS": "DFW",
    "HOUSTON": "IAH", "PHOENIX": "PHX", "LAS VEGAS": "LAS",
    "HONOLULU": "HNL", "VANCOUVER": "YVR",
}


def _resolve_iata(code):
    """Resolve city names or non-standard codes to valid IATA codes."""
    code = code.strip().upper()
    if len(code) == 3 and code.isalpha():
        # Check if it's a city name that maps to a different code
        return CITY_TO_IATA.get(code, code)
    return CITY_TO_IATA.get(code, code)


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    try:
        return store.get_user_by_id(user_id)
    except Exception:
        return None


def require_store():
    if not store.enabled:
        raise RuntimeError("Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")
    store.seed_defaults()


def login_required(view_fn):
    @wraps(view_fn)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            return redirect(url_for("login"))
        return view_fn(*args, **kwargs)

    return wrapper


def admin_required(view_fn):
    @wraps(view_fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return redirect(url_for("login"))
        if user.get("role") != "admin":
            return redirect(url_for("index"))
        return view_fn(*args, **kwargs)

    return wrapper


def get_enabled_models():
    try:
        return store.list_enabled_models()
    except Exception:
        return [model for model in ALL_MODELS if model["id"] in DEFAULT_ENABLED_MODEL_IDS]


def choose_model(requested_model):
    enabled_models = get_enabled_models()
    enabled_ids = {model["id"] for model in enabled_models}
    if requested_model in enabled_ids:
        return requested_model
    if "openai/gpt-5.4" in enabled_ids:
        return "openai/gpt-5.4"
    return enabled_models[0]["id"] if enabled_models else "openai/gpt-5.4"


def bootstrap_payload(user):
    config = store.get_config() if store.enabled else {
        "registration_enabled": True,
        "enabled_models": DEFAULT_ENABLED_MODEL_IDS,
        "registration_password_hash": None,
    }
    return {
        "user": serialize_bootstrap_user(user),
        "models": get_enabled_models(),
        "is_admin": bool(user and user.get("role") == "admin"),
        "registration_enabled": bool(config.get("registration_enabled", True)),
    }


def execute_flight_search(params):
    """Run the actual flight search using existing swoop backend.

    Returns (outbound_flights, return_flights, round_trip_flights, error_string).
    return_flights and round_trip_flights are [] for one-way searches.

    Outbound, return, and round-trip searches run in parallel for speed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    origin = _resolve_iata(params.get("origin", ""))
    dest = _resolve_iata(params.get("destination", ""))
    date_strs = params.get("dates", [])
    max_stops = params.get("max_stops", 1)
    cabin = params.get("cabin", "business")
    is_one_way = params.get("one_way", False)
    return_after_days = params.get("return_after_days")
    return_date_str = params.get("return_date")
    excluded_routing = set(c.upper() for c in params.get("exclude_routing", []))

    if not origin or not dest or not date_strs:
        return [], [], [], "Missing origin, destination, or dates."

    try:
        dates = [date.fromisoformat(d) for d in date_strs]
    except ValueError as e:
        return [], [], [], f"Invalid date format: {e}"

    def _filter_and_limit(raw_results, limit_per_date=5):
        serialized = []
        for d in sorted(raw_results.keys()):
            flights = raw_results.get(d, [])
            flights = apply_all_filters(flights, max_stops=max_stops, excluded_routing=excluded_routing)
            flights.sort(key=lambda f: f.price if f.price is not None else float("inf"))
            serialized.extend(serialize_flights(flights[:limit_per_date]))
        return serialized

    # Build return date mapping
    depart_to_return = {}
    if not is_one_way:
        if return_after_days:
            depart_to_return = {d: d + timedelta(days=return_after_days) for d in dates}
        elif return_date_str:
            try:
                rd = date.fromisoformat(return_date_str)
                depart_to_return = {d: rd for d in dates}
            except ValueError:
                pass

    # --- Launch all searches in parallel ---
    raw_outbound = {}
    raw_return = {}
    raw_round_trip = {}

    def _search_outbound():
        return search_swoop_parallel(origin, dest, dates, max_stops=max_stops, cabin=cabin)

    def _search_return():
        if not depart_to_return:
            return {}
        return_dates = sorted(set(depart_to_return.values()))
        return search_swoop_parallel(dest, origin, return_dates, max_stops=max_stops, cabin=cabin)

    def _search_round_trip():
        if not depart_to_return:
            return {}
        date_pairs = [(dep, depart_to_return[dep]) for dep in sorted(depart_to_return.keys())]
        return search_swoop_roundtrip_parallel(origin, dest, date_pairs, max_stops=max_stops, cabin=cabin)

    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_out = pool.submit(_search_outbound)
        fut_ret = pool.submit(_search_return) if depart_to_return else None
        fut_rt = pool.submit(_search_round_trip) if depart_to_return else None

        try:
            raw_outbound = fut_out.result()
        except Exception as e:
            return [], [], [], f"Search error: {e}"

        if fut_ret:
            try:
                raw_return = fut_ret.result()
            except Exception:
                raw_return = {}

        if fut_rt:
            try:
                raw_round_trip = fut_rt.result()
            except Exception:
                raw_round_trip = {}

    outbound_flights = _filter_and_limit(raw_outbound)

    return_flights = _filter_and_limit(raw_return) if raw_return else []

    round_trip_flights = []
    for dep_date in sorted(raw_round_trip.keys()):
        flights = raw_round_trip.get(dep_date, [])
        flights = apply_all_filters(flights, max_stops=max_stops, excluded_routing=excluded_routing)
        flights.sort(key=lambda f: f.price if f.price is not None else float("inf"))
        serialized = serialize_flights(flights[:3])
        for flight in serialized:
            flight["return_date"] = depart_to_return[dep_date].isoformat()
        round_trip_flights.extend(serialized)

    return outbound_flights, return_flights, round_trip_flights, None


def format_flights_for_chat(outbound, return_flights, search_params, round_trip_flights=None):
    """Format flight results as the SKILL compact list format (plain text)."""
    origin = search_params.get("origin", "")
    dest = search_params.get("destination", "")
    lines = []

    def format_flight_line(f, num):
        stops_str = "Nonstop" if f["stops"] == 0 else f"{f['stops']} stop"
        if f["stops"] > 1:
            stops_str += "s"
        via = ""
        if f.get("layovers"):
            codes = [l["code"] for l in f["layovers"] if l.get("code")]
            if codes:
                via = f" ({', '.join(codes)})"

        price_str = "Price unavailable"
        if f.get("price") is not None:
            curr = f.get("price_currency", "$")
            price_str = f"{curr}{f['price']:,.0f}"

        result = f"{num}. {f['airline']} -- {stops_str}{via} . {f.get('duration', 'N/A')}\n"
        result += f"   {f.get('departure_time', '')} -> {f.get('arrival_time', '')} . {price_str}"

        for lay in f.get("layovers", []):
            if lay.get("duration") and lay.get("code"):
                result += f"\n   Layover: {lay['duration']} {lay['code']}"

        return result

    # Group outbound by date
    if outbound:
        dates_seen = {}
        for f in outbound:
            d = f.get("departure_date", "")
            dates_seen.setdefault(d, []).append(f)

        for d, flights in dates_seen.items():
            date_label = d if d else "Unknown date"
            lines.append(f"--- {origin} -> {dest}: {date_label} ---")
            for i, f in enumerate(flights, 1):
                lines.append(format_flight_line(f, i))
            lines.append("")

    # Group return by date
    if return_flights:
        dates_seen = {}
        for f in return_flights:
            d = f.get("departure_date", "")
            dates_seen.setdefault(d, []).append(f)

        for d, flights in dates_seen.items():
            date_label = d if d else "Unknown date"
            lines.append(f"--- {dest} -> {origin}: {date_label} ---")
            for i, f in enumerate(flights, 1):
                lines.append(format_flight_line(f, i))
            lines.append("")

    # Best combo
    priced_out = [f for f in outbound if f.get("price") is not None] if outbound else []
    priced_ret = [f for f in return_flights if f.get("price") is not None] if return_flights else []
    if priced_out and priced_ret:
        best_out = min(priced_out, key=lambda f: f["price"])
        best_ret = min(priced_ret, key=lambda f: f["price"])
        total = best_out["price"] + best_ret["price"]
        curr = best_out.get("price_currency", "$")
        lines.append(
            f"BEST COMBO: {best_out['airline']} out {curr}{best_out['price']:,.0f} + "
            f"{best_ret['airline']} ret {curr}{best_ret['price']:,.0f} = {curr}{total:,.0f}"
        )

    priced_rt = [f for f in round_trip_flights if f.get("price") is not None] if round_trip_flights else []
    if priced_rt:
        best_rt = min(priced_rt, key=lambda f: f["price"])
        curr = best_rt.get("price_currency", "$")
        return_date = best_rt.get("return_date", search_params.get("return_date", ""))
        lines.append(
            f"BEST PROMO RT: {best_rt['airline']} {curr}{best_rt['price']:,.0f}"
            f" ({best_rt.get('departure_date', '')} -> {return_date})"
        )

    return "\n".join(lines)


def _build_gf_link(dep_airport, arr_airport, dep_date, cabin="business", return_date=None):
    """Build a Google Flights booking URL."""
    trip_part = f"+returning+{return_date}" if return_date else "+one+way"
    return f"https://www.google.com/travel/flights?q=Flights+from+{dep_airport}+to+{arr_airport}+on+{dep_date}{trip_part}+{cabin}+class"


def build_trip_analysis(outbound, return_flights, round_trip_flights, search_params):
    """Build promo round-trip vs combo-flight comparison for the UI."""
    priced_out = [f for f in outbound if f.get("price") is not None]
    priced_ret = [f for f in return_flights if f.get("price") is not None] if return_flights else []
    priced_rt = [f for f in round_trip_flights if f.get("price") is not None] if round_trip_flights else []

    combo = None
    if priced_out and priced_ret:
        cheapest_pair = min(
            (
                (out_flight, ret_flight, out_flight["price"] + ret_flight["price"])
                for out_flight in priced_out
                for ret_flight in priced_ret
            ),
            key=lambda item: item[2],
        )
        cheapest_out, cheapest_ret, total = cheapest_pair
        curr = cheapest_out.get("price_currency", "$")
        combo = {
            "label": "COMBO FLIGHT",
            "total": total,
            "total_formatted": f"{curr}{total:,.0f}",
            "combos_checked": len(priced_out) * len(priced_ret),
            "leg_1": {
                "airline": cheapest_out["airline"],
                "price": cheapest_out["price"],
                "price_formatted": f"{curr}{cheapest_out['price']:,.0f}",
                "route": f"{cheapest_out.get('departure_airport', '')} -> {cheapest_out.get('arrival_airport', '')}",
                "date": cheapest_out.get("departure_date", ""),
                "duration": cheapest_out.get("duration", ""),
                "stops": cheapest_out.get("stops", 0),
                "via": ", ".join(l["code"] for l in cheapest_out.get("layovers", []) if l.get("code")),
                "link": _build_gf_link(
                    cheapest_out.get("departure_airport", ""),
                    cheapest_out.get("arrival_airport", ""),
                    cheapest_out.get("departure_date", ""),
                ),
            },
            "leg_2": {
                "airline": cheapest_ret["airline"],
                "price": cheapest_ret["price"],
                "price_formatted": f"{curr}{cheapest_ret['price']:,.0f}",
                "route": f"{cheapest_ret.get('departure_airport', '')} -> {cheapest_ret.get('arrival_airport', '')}",
                "date": cheapest_ret.get("departure_date", ""),
                "duration": cheapest_ret.get("duration", ""),
                "stops": cheapest_ret.get("stops", 0),
                "via": ", ".join(l["code"] for l in cheapest_ret.get("layovers", []) if l.get("code")),
                "link": _build_gf_link(
                    cheapest_ret.get("departure_airport", ""),
                    cheapest_ret.get("arrival_airport", ""),
                    cheapest_ret.get("departure_date", ""),
                ),
            },
        }

    promo = None
    if priced_rt:
        cheapest_rt = min(priced_rt, key=lambda f: f["price"])
        curr = cheapest_rt.get("price_currency", "$")
        promo = {
            "label": "PROMO ROUND-TRIP",
            "total": cheapest_rt["price"],
            "total_formatted": f"{curr}{cheapest_rt['price']:,.0f}",
            "airline": cheapest_rt["airline"],
            "route": f"{cheapest_rt.get('departure_airport', '')} -> {cheapest_rt.get('arrival_airport', '')}",
            "departure_date": cheapest_rt.get("departure_date", ""),
            "return_date": cheapest_rt.get("return_date", search_params.get("return_date", "")),
            "duration": cheapest_rt.get("duration", ""),
            "stops": cheapest_rt.get("stops", 0),
            "via": ", ".join(l["code"] for l in cheapest_rt.get("layovers", []) if l.get("code")),
            "link": _build_gf_link(
                cheapest_rt.get("departure_airport", ""),
                cheapest_rt.get("arrival_airport", ""),
                cheapest_rt.get("departure_date", ""),
                return_date=cheapest_rt.get("return_date", search_params.get("return_date", "")),
            ),
        }

    if not combo and not promo:
        return None

    winner = "combo"
    if promo and not combo:
        winner = "promo"
    elif promo and combo and promo["total"] < combo["total"]:
        winner = "promo"
    savings = None
    savings_formatted = None
    if combo and promo:
        savings = abs(combo["total"] - promo["total"])
        curr = promo.get("total_formatted", combo.get("total_formatted", "$"))[:1]
        savings_formatted = f"{curr}{savings:,.0f}"

    return {
        "winner": winner,
        "winner_label": "BEST PROMO ROUND-TRIP" if winner == "promo" else "BEST COMBO FLIGHT",
        "promo": promo,
        "combo": combo,
        "savings": savings,
        "savings_formatted": savings_formatted,
    }


def build_best_deal(outbound, return_flights, round_trip_flights, search_params):
    """Build best deal summary with booking links for the chat footer."""
    priced_out = [f for f in outbound if f.get("price") is not None]
    priced_ret = [f for f in return_flights if f.get("price") is not None] if return_flights else []

    if not priced_out:
        return None

    # Cheapest outbound
    cheapest_out = min(priced_out, key=lambda f: f["price"])
    curr = cheapest_out.get("price_currency", "$")

    out_link = _build_gf_link(
        cheapest_out.get("departure_airport", ""),
        cheapest_out.get("arrival_airport", ""),
        cheapest_out.get("departure_date", ""),
    )

    trip_analysis = build_trip_analysis(outbound, return_flights, round_trip_flights, search_params)

    # One-way result
    if not priced_ret:
        if trip_analysis and trip_analysis.get("promo"):
            promo = trip_analysis["promo"]
            return {
                "type": "promo_round_trip",
                "label": "BEST PROMO ROUND-TRIP",
                "total": promo["total"],
                "total_formatted": promo["total_formatted"],
                "promo": promo,
                "comparison": {
                    "winner": "promo",
                    "savings_formatted": trip_analysis.get("savings_formatted"),
                },
            }
        via = ", ".join(l["code"] for l in cheapest_out.get("layovers", []) if l.get("code"))
        return {
            "type": "one_way",
            "label": "BEST DEAL",
            "total": cheapest_out["price"],
            "total_formatted": f"{curr}{cheapest_out['price']:,.0f}",
            "outbound": {
                "airline": cheapest_out["airline"],
                "price": cheapest_out["price"],
                "price_formatted": f"{curr}{cheapest_out['price']:,.0f}",
                "route": f"{cheapest_out.get('departure_airport', '')} -> {cheapest_out.get('arrival_airport', '')}",
                "date": cheapest_out.get("departure_date", ""),
                "duration": cheapest_out.get("duration", ""),
                "stops": cheapest_out.get("stops", 0),
                "via": via,
                "link": out_link,
            },
            "return": None,
        }

    if trip_analysis and trip_analysis.get("winner") == "promo" and trip_analysis.get("promo"):
        promo = trip_analysis["promo"]
        return {
            "type": "promo_round_trip",
            "label": trip_analysis["winner_label"],
            "total": promo["total"],
            "total_formatted": promo["total_formatted"],
            "promo": promo,
            "comparison": {
                "winner": trip_analysis["winner"],
                "savings_formatted": trip_analysis.get("savings_formatted"),
            },
        }

    # Round-trip: cheapest combo
    cheapest_ret = min(priced_ret, key=lambda f: f["price"])
    total = cheapest_out["price"] + cheapest_ret["price"]

    ret_link = _build_gf_link(
        cheapest_ret.get("departure_airport", ""),
        cheapest_ret.get("arrival_airport", ""),
        cheapest_ret.get("departure_date", ""),
    )

    out_via = ", ".join(l["code"] for l in cheapest_out.get("layovers", []) if l.get("code"))
    ret_via = ", ".join(l["code"] for l in cheapest_ret.get("layovers", []) if l.get("code"))

    return {
        "type": "round_trip",
        "label": trip_analysis["winner_label"] if trip_analysis else "BEST COMBO FLIGHT",
        "total": total,
        "total_formatted": f"{curr}{total:,.0f}",
        "outbound": {
            "airline": cheapest_out["airline"],
            "price": cheapest_out["price"],
            "price_formatted": f"{curr}{cheapest_out['price']:,.0f}",
            "route": f"{cheapest_out.get('departure_airport', '')} -> {cheapest_out.get('arrival_airport', '')}",
            "date": cheapest_out.get("departure_date", ""),
            "duration": cheapest_out.get("duration", ""),
            "stops": cheapest_out.get("stops", 0),
            "via": out_via,
            "link": out_link,
        },
        "return": {
            "airline": cheapest_ret["airline"],
            "price": cheapest_ret["price"],
            "price_formatted": f"{curr}{cheapest_ret['price']:,.0f}",
            "route": f"{cheapest_ret.get('departure_airport', '')} -> {cheapest_ret.get('arrival_airport', '')}",
            "date": cheapest_ret.get("departure_date", ""),
            "duration": cheapest_ret.get("duration", ""),
            "stops": cheapest_ret.get("stops", 0),
            "via": ret_via,
            "link": ret_link,
        },
        "comparison": {
            "winner": trip_analysis["winner"] if trip_analysis else "combo",
            "savings_formatted": trip_analysis.get("savings_formatted") if trip_analysis else None,
        },
    }


def serialize_flights(flights):
    """Convert Flight objects to JSON-serializable dicts."""
    return [{
        "airline": f.airline,
        "price": f.price,
        "price_currency": f.price_currency,
        "departure_time": f.departure_time,
        "arrival_time": f.arrival_time,
        "departure_airport": f.departure_airport,
        "arrival_airport": f.arrival_airport,
        "duration": f.duration,
        "stops": f.stops,
        "departure_date": f.departure_date,
        "flight_numbers": f.flight_numbers,
        "aircraft_types": f.aircraft_types,
        "layovers": [
            {"duration": l.duration, "code": l.code, "city": l.city}
            for l in f.layovers
        ],
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
                "seat_type": s.seat_type,
                "has_wifi": s.has_wifi,
                "has_power": s.has_power,
            }
            for s in f.segments
        ],
    } for f in flights]


@app.route("/")
@login_required
def index():
    user = get_current_user()
    return render_template("index.html", bootstrap=bootstrap_payload(user))


@app.route("/login", methods=["GET", "POST"])
def login():
    existing_user = get_current_user()
    if existing_user:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        try:
            require_store()
            user = store.verify_user(username, password)
        except Exception as exc:
            user = None
            error = str(exc)
        if user:
            session["user_id"] = user["id"]
            return redirect(url_for("index"))
        if not error:
            error = "Invalid username or password."
    return render_template("login.html", error=error, store_configured=store.enabled)


@app.route("/register", methods=["GET", "POST"])
def register():
    existing_user = get_current_user()
    if existing_user:
        return redirect(url_for("index"))

    error = None
    config = {"registration_enabled": True, "registration_password_hash": None}
    try:
        require_store()
        config = store.get_config()
    except Exception as exc:
        error = str(exc)

    if request.method == "POST" and not error:
        if not config.get("registration_enabled", True):
            error = "Registration is currently disabled."
        else:
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            registration_password = request.form.get("registration_password", "")
            if not username or not password:
                error = "Username and password are required."
            elif store.get_user_by_username(username):
                error = "Username is already taken."
            elif config.get("registration_password_hash") and not store.verify_registration_password(registration_password):
                error = "Registration password is invalid."
            else:
                user = store.create_user(username, password, role="member")
                session["user_id"] = user["id"]
                return redirect(url_for("index"))

    return render_template(
        "register.html",
        error=error,
        registration_enabled=config.get("registration_enabled", True),
        registration_requires_password=bool(config.get("registration_password_hash")),
        store_configured=store.enabled,
    )


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    user = get_current_user()
    config = store.get_config()
    users = store.list_users()
    chat_logs = store.list_chat_logs(limit=100)
    admin_logs = store.list_admin_logs(limit=100)
    return render_template(
        "admin.html",
        bootstrap=bootstrap_payload(user),
        config=config,
        users=users,
        chat_logs=chat_logs,
        admin_logs=admin_logs,
        all_models=ALL_MODELS,
    )


@app.route("/api/bootstrap")
@login_required
def api_bootstrap():
    return jsonify(bootstrap_payload(get_current_user()))


@app.route("/api/admin/settings", methods=["POST"])
@admin_required
def admin_update_settings():
    user = get_current_user()
    data = request.get_json() or {}
    enabled_models = data.get("enabled_models") or []
    enabled_ids = {model["id"] for model in ALL_MODELS}
    enabled_models = [model_id for model_id in enabled_models if model_id in enabled_ids]
    if not enabled_models:
        enabled_models = ["openai/gpt-5.4"]

    updates = {
        "registration_enabled": bool(data.get("registration_enabled", True)),
        "enabled_models": enabled_models,
    }

    registration_password = (data.get("registration_password") or "").strip()
    if data.get("clear_registration_password"):
        updates["registration_password_hash"] = None
    elif registration_password:
        updates["registration_password_hash"] = store.set_registration_password(registration_password).get("registration_password_hash")

    config = store.update_config(updates)
    store.log_admin_action(
        admin_user_id=user["id"],
        admin_username=user["username"],
        action="update_settings",
        target_type="app_config",
        target_id="main",
        details={
            "registration_enabled": config.get("registration_enabled"),
            "enabled_models": config.get("enabled_models"),
            "registration_password_set": bool(config.get("registration_password_hash")),
        },
    )
    return jsonify({"ok": True, "config": config})


@app.route("/api/admin/users/<user_id>", methods=["DELETE"])
@admin_required
def admin_delete_user(user_id):
    user = get_current_user()
    target = store.get_user_by_id(user_id)
    if not target:
        return jsonify({"error": "User not found."}), 404
    if target["id"] == user["id"]:
        return jsonify({"error": "Admin cannot delete the current logged-in account."}), 400
    store.delete_user(user_id)
    store.log_admin_action(
        admin_user_id=user["id"],
        admin_username=user["username"],
        action="delete_user",
        target_type="app_user",
        target_id=user_id,
        details={"username": target.get("username"), "role": target.get("role")},
    )
    return jsonify({"ok": True})


@app.route("/api/admin/logs")
@admin_required
def admin_logs_api():
    return jsonify({
        "chat_logs": store.list_chat_logs(limit=200),
        "admin_logs": store.list_admin_logs(limit=200),
        "users": store.list_users(),
        "config": store.get_config(),
    })


def _build_log_payload(user_messages, model, user_context, response_payload, search_time=None):
    """Build a compact log payload for Supabase — only the latest prompt + summary stats."""
    latest_user_msg = ""
    for m in reversed(user_messages):
        if m.get("role") == "user":
            latest_user_msg = m.get("content", "")
            break

    req_log = {
        "prompt": latest_user_msg[:500],
        "model": model,
        "message_count": len(user_messages),
    }
    if user_context:
        req_log["user_context"] = user_context[:200]

    resp_log = {
        "message": (response_payload.get("message") or "")[:500],
    }
    if response_payload.get("search_params"):
        sp = response_payload["search_params"]
        resp_log["search"] = {
            "origin": sp.get("origin"),
            "destination": sp.get("destination"),
            "dates": sp.get("dates", []),
            "cabin": sp.get("cabin", "business"),
            "max_stops": sp.get("max_stops"),
            "return_after_days": sp.get("return_after_days"),
        }
    if response_payload.get("flights") is not None:
        resp_log["outbound_count"] = len(response_payload["flights"])
    if response_payload.get("return_flights") is not None:
        resp_log["return_count"] = len(response_payload["return_flights"])
    if response_payload.get("round_trip_flights") is not None:
        resp_log["rt_promo_count"] = len(response_payload["round_trip_flights"])
    if response_payload.get("best_deal"):
        bd = response_payload["best_deal"]
        resp_log["best_deal"] = f"{bd.get('label', '')} {bd.get('total_formatted', '')}"
    if search_time is not None:
        resp_log["search_time_s"] = round(search_time, 1)

    return req_log, resp_log


@app.route("/api/chat", methods=["POST"])
@login_required
def chat():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    current_user = get_current_user()
    user_messages = data.get("messages", [])
    model = choose_model(data.get("model", "openai/gpt-5.4"))
    user_context = data.get("user_context", "")
    client_session_id = data.get("client_session_id")

    # Build conversation with system prompt + user preferences
    system_prompt = get_system_prompt()
    if user_context:
        system_prompt += (
            f"\n\n=====================\nUSER PREFERENCES (from local memory)\n"
            f"=====================\n"
            f"This returning user has saved preferences. USE THEM AS DEFAULTS — do NOT re-ask:\n"
            f"- Cabin class is saved → use it, do NOT ask again.\n"
            f"- Trip type is saved → use it, do NOT ask again.\n"
            f"- If frequent origin is saved and user didn't specify origin → use the first frequent origin automatically.\n"
            f"- Only ask about what is TRULY missing (not covered by prefs or message).\n"
            f"- If all 4 requirements (location, date, cabin, trip type) are covered between the message and saved preferences, SEARCH IMMEDIATELY.\n"
            f"- Briefly note: 'Searching [cabin] [trip-type] from [origin]...'\n\n"
            f"Saved preferences:\n{user_context}"
        )
    messages = [{"role": "system", "content": system_prompt}]
    # Inject user preferences as context before first message
    if user_context:
        messages.append({
            "role": "assistant",
            "content": f"I see you're a returning user. I have your preferences loaded: {user_context} I'll use these as defaults so you don't need to repeat them.",
        })
    messages.extend(user_messages)

    import time as _time
    _t_start = _time.time()

    # Phase 1: Call AI
    result = call_openrouter(messages, model)

    if "error" in result:
        response_payload = {
            "message": f"Sorry, there was an error connecting to the AI: {result['error']}",
            "flights": None,
            "search_params": None,
            "return_flights": None,
            "round_trip_flights": None,
            "trip_analysis": None,
            "best_deal": None,
        }
        if current_user and store.enabled:
            req_log, resp_log = _build_log_payload(user_messages, model, user_context, response_payload)
            resp_log["error"] = result["error"][:200]
            store.log_chat_event(
                user_id=current_user["id"],
                username=current_user["username"],
                role=current_user["role"],
                session_id=client_session_id,
                request_payload=req_log,
                response_payload=resp_log,
            )
        return jsonify(response_payload)

    choice = result.get("choices", [{}])[0]
    msg = choice.get("message", {})
    content = msg.get("content", "")
    tool_calls = msg.get("tool_calls", [])

    # Check for tool calls (native tool calling)
    search_params = None
    if tool_calls:
        for tc in tool_calls:
            if tc.get("function", {}).get("name") == "search_flights":
                try:
                    search_params = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    pass
                break

    # Fallback: check if AI embedded JSON in its text response
    if not search_params and content:
        search_params = extract_tool_call_from_text(content)

    # If we have search params, execute the search
    if search_params:
        _t_search = _time.time()
        outbound, return_flights, round_trip_flights, error = execute_flight_search(search_params)
        _search_elapsed = _time.time() - _t_search

        if error:
            response_payload = {
                "message": f"I tried to search but encountered an issue: {error}",
                "flights": None,
                "return_flights": None,
                "round_trip_flights": None,
                "search_params": search_params,
                "trip_analysis": None,
                "best_deal": None,
            }
            if current_user and store.enabled:
                req_log, resp_log = _build_log_payload(user_messages, model, user_context, response_payload, _search_elapsed)
                resp_log["error"] = error[:200]
                store.log_chat_event(
                    user_id=current_user["id"],
                    username=current_user["username"],
                    role=current_user["role"],
                    session_id=client_session_id,
                    request_payload=req_log,
                    response_payload=resp_log,
                )
            return jsonify(response_payload)

        if not outbound and not return_flights and not round_trip_flights:
            summary_msg = f"No flights found for {search_params.get('origin', '?')} to {search_params.get('destination', '?')} on the requested dates. Try different dates or nearby airports."
            response_payload = {
                "message": summary_msg,
                "flights": [],
                "return_flights": [],
                "round_trip_flights": [],
                "search_params": search_params,
                "trip_analysis": None,
                "best_deal": None,
            }
            if current_user and store.enabled:
                req_log, resp_log = _build_log_payload(user_messages, model, user_context, response_payload, _search_elapsed)
                store.log_chat_event(
                    user_id=current_user["id"],
                    username=current_user["username"],
                    role=current_user["role"],
                    session_id=client_session_id,
                    request_payload=req_log,
                    response_payload=resp_log,
                )
            return jsonify(response_payload)

        # Phase 2: Build human-readable summary from the flight data
        # Format it ourselves in the SKILL compact list format so the AI
        # doesn't need to interpret raw JSON (which causes JSON echo issues)
        summary_content = format_flights_for_chat(
            outbound, return_flights, search_params, round_trip_flights
        )

        # Ask AI for a brief intro + flight advice at the end
        origin = search_params.get("origin", "")
        dest = search_params.get("destination", "")
        cabin = search_params.get("cabin", "business")
        num_out = len(outbound)
        num_ret = len(return_flights) if return_flights else 0
        num_rt = len(round_trip_flights) if round_trip_flights else 0
        cheapest_price = ""
        if outbound:
            priced = [f for f in outbound if f.get("price") is not None]
            if priced:
                best = min(priced, key=lambda f: f["price"])
                cheapest_price = f"{best.get('price_currency','$')}{best['price']:,.0f} on {best.get('airline','')}"

        messages.append({
            "role": "assistant",
            "content": content or "Let me search for those flights.",
        })
        messages.append({
            "role": "user",
            "content": (
                f"[SYSTEM: Flight search completed. {num_out} outbound and {num_ret} return flights found. "
                f"{num_rt} round-trip promo fares found. "
                f"Route: {origin} to {dest}. Cabin: {cabin}. "
                f"{'Cheapest outbound: ' + cheapest_price + '. ' if cheapest_price else ''}"
                "Write a SHORT response (3-4 sentences total, NO labels, NO headings, just flowing text):\n\n"
                "Sentence 1-2: Briefly highlight the best deal found.\n"
                "Sentence 3: A SHORT specific travel tip for this route (best layover airports, "
                "day-of-week savings, alternate airports, airline product tips, connection warnings, "
                "or booking timing). Make it useful and specific to this route.\n"
                "Sentence 4: End with ONE follow-up question to help get better results, like: "
                "'Want me to check nearby dates for a better price?' or "
                "'Should I search nonstop-only options?' or "
                "'Would you like me to compare with a different cabin?'\n\n"
                "Do NOT use labels like 'PART 1' or 'INTRO'. Do NOT list flights. "
                "Do NOT output JSON. Just write natural flowing sentences.]"
            ),
        })

        intro_result = call_openrouter(messages, model)
        intro_content = ""
        if "choices" in intro_result:
            intro_content = intro_result["choices"][0].get("message", {}).get("content", "")

        # If AI still outputs JSON or garbage, use our formatted summary
        if not intro_content:
            intro_content = summary_content
        else:
            intro_content = intro_content.strip()
            # Detect if AI dumped JSON instead of a real response
            stripped = intro_content.lstrip()
            if (stripped.startswith("{") or stripped.startswith("[") or
                    '"action"' in intro_content[:100] or
                    '"airline"' in intro_content[:100] or
                    '```json' in intro_content[:50]):
                intro_content = summary_content

        # Build best deal summary with booking links
        trip_analysis = build_trip_analysis(outbound, return_flights, round_trip_flights, search_params)
        best_deal = build_best_deal(outbound, return_flights, round_trip_flights, search_params)

        response_payload = {
            "message": intro_content,
            "flights": outbound,
            "return_flights": return_flights,
            "round_trip_flights": round_trip_flights,
            "search_params": search_params,
            "trip_analysis": trip_analysis,
            "best_deal": best_deal,
        }
        if current_user and store.enabled:
            _total_elapsed = _time.time() - _t_start
            req_log, resp_log = _build_log_payload(user_messages, model, user_context, response_payload, _search_elapsed)
            resp_log["total_time_s"] = round(_total_elapsed, 1)
            store.log_chat_event(
                user_id=current_user["id"],
                username=current_user["username"],
                role=current_user["role"],
                session_id=client_session_id,
                request_payload=req_log,
                response_payload=resp_log,
            )
        return jsonify(response_payload)

    # No search needed - just a conversational response
    response_payload = {
        "message": content or "I'm not sure how to respond to that. Try asking me to search for flights!",
        "flights": None,
        "return_flights": None,
        "round_trip_flights": None,
        "search_params": None,
        "trip_analysis": None,
        "best_deal": None,
    }
    if current_user and store.enabled:
        _total_elapsed = _time.time() - _t_start
        req_log, resp_log = _build_log_payload(user_messages, model, user_context, response_payload)
        resp_log["total_time_s"] = round(_total_elapsed, 1)
        store.log_chat_event(
            user_id=current_user["id"],
            username=current_user["username"],
            role=current_user["role"],
            session_id=client_session_id,
            request_payload=req_log,
            response_payload=resp_log,
        )
    return jsonify(response_payload)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
