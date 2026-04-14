"""Format structured flight data into Signal-ready text output."""

import re
from datetime import date as date_type
from .parser import Flight, Segment
from .config import SIGNAL_MSG_LIMIT, FLIGHTS_PER_MESSAGE, MAX_FLIGHTS_PER_DIRECTION


def format_price(flight: Flight) -> str:
    """Format price for display."""
    if flight.price_unavailable:
        return "PRICE UNAVAILABLE"
    if flight.price is None:
        return "N/A"
    p = f"{flight.price:,.0f}"
    return f"{flight.price_currency}{p}" if flight.price_currency in ("$",) else f"{flight.price_currency} {p}"


def format_flight(flight: Flight, number: int) -> str:
    """Format a single flight into compact list format."""
    # Line 1: Airline — Stops · Duration
    if flight.stops == 0:
        stops_str = "Nonstop"
    else:
        via = ", ".join(lay.code for lay in flight.layovers) if flight.layovers else ""
        stops_str = f"{flight.stops} stop{'s' if flight.stops > 1 else ''}"
        if via:
            stops_str += f" ({via})"

    line1 = f"{number}. {flight.airline} — {stops_str} · {flight.duration}"

    # Line 2: Departure → Arrival · Price
    time_str = f"{flight.departure_time} → {flight.arrival_time}"
    if flight.departure_date:
        time_str = f"{flight.departure_date} {time_str}"
    price_str = format_price(flight)
    if flight.is_round_trip:
        price_str += " (RT total)"
    line2 = f"   {time_str} · {price_str}"

    lines = [line1, line2]

    # Line 3+: Layover details (if any)
    for lay in flight.layovers:
        lines.append(f"   Layover: {lay.duration} {lay.code}")

    # Flight numbers (if available)
    if flight.flight_numbers:
        lines.append(f"   Flights: {', '.join(flight.flight_numbers)}")

    # Aircraft types (if available)
    if flight.aircraft_types:
        unique_aircraft = list(dict.fromkeys(flight.aircraft_types))
        lines.append(f"   Aircraft: {', '.join(unique_aircraft)}")

    return "\n".join(lines)


def format_direction_results(
    direction_label: str,
    flights: list[Flight],
    max_shown: int | None = None,
) -> str:
    """Format results for one direction (e.g., 'OUTBOUND: BOM → LAX (Apr 7)').

    Shows up to max_shown flights (default MAX_FLIGHTS_PER_DIRECTION).
    If more exist, appends a '+ N more' note.
    """
    if not flights:
        return f"{direction_label}\nNo suitable flights found (excluding blocked airlines).\n"

    limit = max_shown or MAX_FLIGHTS_PER_DIRECTION
    shown = flights[:limit]
    remaining = len(flights) - limit

    lines = [direction_label]
    for i, f in enumerate(shown, 1):
        lines.append(format_flight(f, i))
        lines.append("")  # blank line between flights
    if remaining > 0:
        lines.append(f"   + {remaining} more option{'s' if remaining > 1 else ''} (not shown)")
        lines.append("")
    return "\n".join(lines)


def _format_date_weekday(iso_date: str) -> str:
    """Convert '2026-04-07' to 'Apr 07 (Tue)'."""
    try:
        d = date_type.fromisoformat(iso_date)
        return d.strftime("%b %d (%a)")
    except (ValueError, TypeError):
        return iso_date


def _format_leg_detailed(label: str, flight: Flight, indent: str = "") -> list[str]:
    """Format a flight leg with per-segment details in VOC-Flight style.

    Example output:
    ✈️ OUTBOUND · Apr 07 (Tue)
    BOM → CDG → LAX · 24h 5m

    Leg 1: AF217 · Boeing 777
    BOM 1:20 AM → CDG 7:25 AM
    9h 35m · Lie-flat seat, free Wi-Fi

    2h 55m layover at Paris (CDG)

    Leg 2: AF22 · Boeing 777
    CDG 10:20 AM → LAX 12:55 PM
    11h 35m · Lie-flat seat, free Wi-Fi
    """
    lines = []

    # Header: ✈️ OUTBOUND · Apr 07 (Tue)
    date_str = _format_date_weekday(flight.departure_date) if flight.departure_date else ""
    header = f"{indent}✈️ {label}"
    if date_str:
        header += f" · {date_str}"
    lines.append(header)

    # Route summary: BOM → CDG → LAX · 24h 5m
    if flight.segments:
        route_parts = [flight.segments[0].departure_airport]
        for seg in flight.segments:
            route_parts.append(seg.arrival_airport)
        route = " → ".join(route_parts)
    else:
        route = f"{flight.departure_airport} → {flight.arrival_airport}"
    lines.append(f"{indent}{route} · {flight.duration}")

    # Per-segment details
    if flight.segments:
        for seg_idx, seg in enumerate(flight.segments, 1):
            lines.append("")
            # Leg N: AF217 · Boeing 777
            seg_header = f"{indent}Leg {seg_idx}: {seg.flight_number}"
            if seg.aircraft:
                seg_header += f" · {seg.aircraft}"
            lines.append(seg_header)

            # BOM 1:20 AM → CDG 7:25 AM
            lines.append(f"{indent}{seg.departure_airport} {seg.departure_time} → {seg.arrival_airport} {seg.arrival_time}")

            # 9h 35m · Lie-flat seat, free Wi-Fi
            details = []
            if seg.duration:
                details.append(seg.duration)
            if seg.seat_type:
                details.append(seg.seat_type)
            amenities = []
            if seg.has_wifi:
                amenities.append("free Wi-Fi")
            if seg.has_power:
                amenities.append("power outlet")
            if amenities:
                details.append(", ".join(amenities))
            if details:
                lines.append(f"{indent}{' · '.join(details)}")

            # Layover after this segment (if not last segment)
            if seg_idx <= len(flight.layovers):
                lay = flight.layovers[seg_idx - 1]
                lay_desc = f"{indent}\n{indent}{lay.duration} layover"
                if lay.city and lay.code:
                    lay_desc += f" at {lay.city} ({lay.code})"
                elif lay.code:
                    lay_desc += f" at {lay.code}"
                lines.append(lay_desc)
    else:
        # Fallback: no segment data, show summary
        lines.append("")
        fn_str = ", ".join(flight.flight_numbers) if flight.flight_numbers else ""
        ac_str = ", ".join(dict.fromkeys(flight.aircraft_types)) if flight.aircraft_types else ""
        if fn_str:
            lines.append(f"{indent}Flights: {fn_str}")
        if ac_str:
            lines.append(f"{indent}Aircraft: {ac_str}")
        lines.append(f"{indent}{flight.departure_airport} {flight.departure_time} → {flight.arrival_airport} {flight.arrival_time}")
        if flight.layovers:
            for lay in flight.layovers:
                lines.append(f"{indent}{lay.duration} layover at {lay.code}")

    return lines


def _find_best_return(airline: str, return_flights: list[Flight]) -> Flight | None:
    """Find the best return flight matching the outbound airline."""
    # Exact match
    exact = [f for f in return_flights if f.airline == airline and f.price is not None]
    if exact:
        return min(exact, key=lambda f: f.price)

    # Partial match (e.g., "Air France" in "Air France + KLM")
    airline_lower = airline.lower()
    partial = [
        f for f in return_flights
        if f.price is not None and (
            airline_lower in f.airline.lower()
            or any(part.strip().lower() in airline_lower for part in f.airline.split("+"))
        )
    ]
    if partial:
        return min(partial, key=lambda f: f.price)

    return None


def format_combined_rt(
    rt_label: str,
    rt_flights: list[Flight],
    return_flights: list[Flight],
    return_date: str = "",
) -> str:
    """Format RT results with detailed per-segment breakdown."""
    if not rt_flights:
        return f"{rt_label}\nNo round-trip fares found.\n"

    limit = MAX_FLIGHTS_PER_DIRECTION
    shown_rt = rt_flights[:limit]
    remaining = len(rt_flights) - limit

    lines = [rt_label]
    for i, rt in enumerate(shown_rt, 1):
        price_str = format_price(rt)
        lines.append(f"\n{i}. {rt.airline} — {price_str} (round-trip fare)")
        lines.append("")

        # Outbound leg — detailed
        lines.extend(_format_leg_detailed("OUTBOUND", rt))

        # Return leg — find best same-airline return
        best_ret = _find_best_return(rt.airline, return_flights) if return_flights else None
        if best_ret:
            lines.append("")
            lines.extend(_format_leg_detailed("RETURN", best_ret))
        else:
            lines.append("")
            lines.append(f"✈️ RETURN: {rt.airline} (see return options on Google Flights)")

        # Google Flights link
        if rt.departure_date and rt.departure_airport and rt.arrival_airport:
            dep_date = rt.departure_date
            ret_part = f"+returning+{return_date}" if return_date else ""
            gf_url = f"https://www.google.com/travel/flights?q=Flights+from+{rt.departure_airport}+to+{rt.arrival_airport}+on+{dep_date}{ret_part}+business+class"
            lines.append(f"\nBook: {gf_url}")

        lines.append("")

    if remaining > 0:
        lines.append(f"   + {remaining} more RT option{'s' if remaining > 1 else ''} (not shown)")
        lines.append("")

    return "\n".join(lines)


def format_date_pair(
    date_label: str,
    outbound_label: str,
    outbound_flights: list[Flight],
    return_label: str,
    return_flights: list[Flight],
    rt_flights: list[Flight] | None = None,
    rt_return_date: str = "",
) -> str:
    """Format a complete date pair (outbound + return + RT) with best combo."""
    sections = [f"=== {date_label} [Business Class] ===", ""]

    # Outbound (one-way)
    sections.append(format_direction_results(f"ONE-WAY {outbound_label}", outbound_flights))

    # Return (one-way)
    sections.append(format_direction_results(f"ONE-WAY {return_label}", return_flights))

    # Round-trip (combined outbound + return)
    if rt_flights is not None:
        rt_label = outbound_label.replace("OUTBOUND", "ROUND TRIP")
        if rt_return_date:
            rt_label += f" ↔ {rt_return_date}"
        sections.append(format_combined_rt(rt_label, rt_flights, return_flights))

    # Best one-way combo
    best_out = min(
        (f for f in outbound_flights if f.price is not None),
        key=lambda f: f.price,
        default=None,
    )
    best_ret = min(
        (f for f in return_flights if f.price is not None),
        key=lambda f: f.price,
        default=None,
    )
    ow_total = None
    if best_out and best_ret:
        ow_total = best_out.price + best_ret.price
        sections.append(
            f"BEST ONE-WAY COMBO: {best_out.airline} out {format_price(best_out)} + "
            f"{best_ret.airline} ret {format_price(best_ret)} = "
            f"{best_out.price_currency}{ow_total:,.0f}"
        )

    # Best RT
    best_rt = None
    if rt_flights:
        best_rt = min(
            (f for f in rt_flights if f.price is not None),
            key=lambda f: f.price,
            default=None,
        )
        if best_rt:
            sections.append(
                f"BEST ROUND TRIP: {best_rt.airline} — {format_price(best_rt)} (RT total)"
            )

    # Winner
    if ow_total is not None and best_rt and best_rt.price is not None:
        if best_rt.price < ow_total:
            saving = ow_total - best_rt.price
            sections.append(f">>> RT saves ${saving:,.0f} vs one-way combo")
        else:
            saving = best_rt.price - ow_total
            sections.append(f">>> One-way combo saves ${saving:,.0f} vs RT")

    sections.append("")
    return "\n".join(sections)


def format_oneway_date_results(
    date_label: str,
    direction_label: str,
    flights: list[Flight],
    max_shown: int | None = None,
) -> str:
    """Format results for a single one-way date search."""
    sections = [f"--- {date_label} [Business Class] ---"]
    if not flights:
        sections.append("No suitable flights found (excluding blocked airlines).")
        sections.append("")
        return "\n".join(sections)

    limit = max_shown or MAX_FLIGHTS_PER_DIRECTION
    shown = flights[:limit]
    remaining = len(flights) - limit

    for i, f in enumerate(shown, 1):
        sections.append(format_flight(f, i))
        sections.append("")
    if remaining > 0:
        sections.append(f"   + {remaining} more option{'s' if remaining > 1 else ''} (not shown)")
        sections.append("")
    return "\n".join(sections)


def format_rt_only(
    date_label: str,
    rt_flights: list[Flight],
    return_flights: list[Flight] | None = None,
    cabin: str = "Business",
    return_date: str = "",
) -> str:
    """Format round-trip results only (no separate one-way sections)."""
    sections = [f"=== {date_label} [{cabin} Class] ==="]
    sections.append(format_combined_rt("", rt_flights, return_flights or [], return_date=return_date))

    # Best RT
    if rt_flights:
        best_rt = min(
            (f for f in rt_flights if f.price is not None),
            key=lambda f: f.price,
            default=None,
        )
        if best_rt:
            sections.append(
                f"BEST: {best_rt.airline} — {format_price(best_rt)} (RT total)"
            )
            sections.append("")

    return "\n".join(sections)


def chunk_for_signal(text: str) -> list[str]:
    """Split text into chunks that fit within Signal's message limit.

    Splits on flight block boundaries (double newlines) to avoid breaking
    a single flight entry across two messages.
    """
    if len(text) <= SIGNAL_MSG_LIMIT:
        return [text]

    # Split into flight blocks (separated by blank lines)
    blocks = re.split(r"\n\n+", text)

    chunks = []
    current = ""

    for block in blocks:
        candidate = (current + "\n\n" + block).strip() if current else block
        if len(candidate) > SIGNAL_MSG_LIMIT - 50:
            # Current chunk is full — flush it
            if current.strip():
                chunks.append(current.strip() + "\n\nMore results coming...")
            current = block
        else:
            current = candidate

    if current.strip():
        chunks.append(current.strip())

    # Add booking prompt to last chunk
    if chunks:
        chunks[-1] += "\n\nWant booking links for any of these? Just say which one."

    return chunks


def format_best_picks_summary(
    all_outbound: list[Flight],
    all_return: list[Flight],
) -> str:
    """Generate a best picks summary across all dates."""
    lines = ["BEST PICKS ACROSS ALL DATES:", ""]

    priced_out = [f for f in all_outbound if f.price is not None]
    priced_ret = [f for f in all_return if f.price is not None]

    if priced_out:
        cheapest_out = min(priced_out, key=lambda f: f.price)
        via = ""
        if cheapest_out.layovers:
            via = f", via {', '.join(l.code for l in cheapest_out.layovers)}"
        fn = ""
        if cheapest_out.flight_numbers:
            fn = f" [{', '.join(cheapest_out.flight_numbers)}]"
        lines.append(
            f"Cheapest outbound: {cheapest_out.airline}{fn} — "
            f"{format_price(cheapest_out)} ({cheapest_out.duration}{via})"
        )

    if priced_ret:
        cheapest_ret = min(priced_ret, key=lambda f: f.price)
        via = ""
        if cheapest_ret.layovers:
            via = f", via {', '.join(l.code for l in cheapest_ret.layovers)}"
        fn = ""
        if cheapest_ret.flight_numbers:
            fn = f" [{', '.join(cheapest_ret.flight_numbers)}]"
        lines.append(
            f"Cheapest return: {cheapest_ret.airline}{fn} — "
            f"{format_price(cheapest_ret)} ({cheapest_ret.duration}{via})"
        )

    if priced_out and priced_ret:
        cheapest_out = min(priced_out, key=lambda f: f.price)
        cheapest_ret = min(priced_ret, key=lambda f: f.price)
        total = cheapest_out.price + cheapest_ret.price
        lines.append(
            f"Cheapest combined: {cheapest_out.price_currency}{total:,.0f} "
            f"({cheapest_out.airline} + {cheapest_ret.airline})"
        )

    # Fastest outbound
    if priced_out:
        from .parser import _parse_duration_to_minutes
        fastest = min(priced_out, key=lambda f: _parse_duration_to_minutes(f.duration) if f.duration else 9999)
        via = ""
        if fastest.layovers:
            via = f", via {', '.join(l.code for l in fastest.layovers)}"
        fn = ""
        if fastest.flight_numbers:
            fn = f" [{', '.join(fastest.flight_numbers)}]"
        lines.append(
            f"Fastest outbound: {fastest.airline}{fn} — "
            f"{format_price(fastest)} ({fastest.duration}{via})"
        )

    lines.append("")
    return "\n".join(lines)
