"""Comprehensive flight search for multi-leg itineraries (config-driven)."""

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from datetime import date
from flight_search.swoop_backend import search_swoop_parallel
from flight_search.filters import apply_all_filters
from flight_search.formatter import format_flight, format_best_picks_summary, format_price
from flight_search.parser import _parse_duration_to_minutes
from flight_search.config import MAX_FLIGHTS_PER_DIRECTION

OUTPUT = []


def out(text=""):
    OUTPUT.append(text)
    print(text)


# ---------------------------------------------------------------------------
# Itinerary configuration — edit this to change the search
# ---------------------------------------------------------------------------

LEGS = [
    {
        "name": "LEG 1",
        "label": "BOM -> LAX (Early April 2026)",
        "origin": "BOM",
        "dest": "LAX",
        "dates": [date(2026, 4, d) for d in range(1, 11)],  # Apr 1-10
        "max_stops": 1,
        "max_layover": None,
        "excluded_routing": {
            "DXB", "AUH", "DOH", "BAH", "AMM", "MCT", "KWI",
            "RUH", "JED", "DAM", "BGW", "BSR", "SHJ", "MHD",
        },
        "notes": "Business class, NO Middle East routing. 'Wrong way' through Asia allowed if fare is good.",
        "direction_prefix": "OUTBOUND",
    },
    {
        "name": "LEG 2",
        "label": "LAX -> BOM (Early May 2026)",
        "origin": "LAX",
        "dest": "BOM",
        "dates": [date(2026, 5, d) for d in range(7, 11)],  # May 7-10
        "max_stops": 1,
        "max_layover": None,
        "excluded_routing": set(),
        "notes": "Business class, must arrive BOM by May 12th. Middle East routing OK.",
        "direction_prefix": "RETURN",
    },
    {
        "name": "LEG 3 OUT",
        "label": "DXB -> JNB (Early June 2026)",
        "origin": "DXB",
        "dest": "JNB",
        "dates": [date(2026, 6, d) for d in range(5, 8)],  # Jun 5-7
        "max_stops": 1,
        "max_layover": None,
        "excluded_routing": set(),
        "notes": "Business class, ~21 days on ground in JNB.",
        "direction_prefix": "DXB->JNB",
    },
    {
        "name": "LEG 3 RET",
        "label": "JNB -> DXB (Late June 2026)",
        "origin": "JNB",
        "dest": "DXB",
        "dates": [date(2026, 6, d) for d in range(26, 29)],  # Jun 26-28
        "max_stops": 1,
        "max_layover": None,
        "excluded_routing": set(),
        "notes": "Business class, return from JNB.",
        "direction_prefix": "JNB->DXB",
    },
]

# Which legs to combine for round-trip combo analysis
COMBOS = [
    {"name": "BOM<->LAX", "outbound_leg": 0, "return_leg": 1},
    {"name": "DXB<->JNB", "outbound_leg": 2, "return_leg": 3},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def dedup_flights(flights):
    """Remove duplicate flights based on flight numbers + departure date."""
    seen = set()
    unique = []
    for f in flights:
        key = (f.ref or f"{f.airline}|{f.departure_time}", f.departure_date)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def search_leg(leg):
    """Search a single leg (parallel), apply filters, return sorted flights."""
    raw = search_swoop_parallel(
        leg["origin"], leg["dest"], leg["dates"],
        max_stops=leg["max_stops"],
    )

    all_flights = []
    for d in leg["dates"]:
        flights = raw.get(d, [])
        print(f"  [{leg['origin']}->{leg['dest']} {d}: {len(flights)} raw]", file=sys.stderr)

        flights = apply_all_filters(
            flights,
            max_stops=leg["max_stops"],
            max_layover_hours=leg.get("max_layover"),
            excluded_routing=leg.get("excluded_routing", set()),
        )
        flights.sort(key=lambda f: f.price if f.price is not None else float("inf"))

        out(f"\n--- {leg['direction_prefix']}: {leg['origin']} -> {leg['dest']}: {d.strftime('%B %d, %Y')} ---")
        if not flights:
            out("No suitable flights found (excluding blocked airlines/routing).")
        else:
            shown = flights[:MAX_FLIGHTS_PER_DIRECTION]
            remaining = len(flights) - MAX_FLIGHTS_PER_DIRECTION
            for i, f in enumerate(shown, 1):
                out(format_flight(f, i))
                out()
            if remaining > 0:
                out(f"   + {remaining} more option{'s' if remaining > 1 else ''} (not shown)")
                out()
            all_flights.extend(flights)

    return dedup_flights(all_flights)


def print_leg_summary(name, flights):
    """Print cheapest/fastest summary for a leg."""
    priced = [f for f in flights if f.price is not None]
    if not priced:
        return

    out(f"\n--- {name} SUMMARY ---")
    cheapest = min(priced, key=lambda f: f.price)
    fastest = min(priced, key=lambda f: _parse_duration_to_minutes(f.duration) if f.duration else 9999)

    c_fn = f" [{', '.join(cheapest.flight_numbers)}]" if cheapest.flight_numbers else ""
    f_fn = f" [{', '.join(fastest.flight_numbers)}]" if fastest.flight_numbers else ""

    out(f"Cheapest: {cheapest.airline}{c_fn} - {format_price(cheapest)} ({cheapest.duration}, {cheapest.stops} stop(s))")
    out(f"Fastest:  {fastest.airline}{f_fn} - {format_price(fastest)} ({fastest.duration}, {fastest.stops} stop(s))")

    top5 = sorted(priced, key=lambda f: f.price)[:5]
    out(f"\nTop 5 Cheapest {name} (all dates):")
    for i, f in enumerate(top5, 1):
        via = ", ".join(l.code for l in f.layovers) if f.layovers else "direct"
        fn = f" [{', '.join(f.flight_numbers)}]" if f.flight_numbers else ""
        ac = f" | {', '.join(dict.fromkeys(f.aircraft_types))}" if f.aircraft_types else ""
        out(f"  {i}. {f.airline}{fn} - {format_price(f)} | {f.departure_date} {f.departure_time} -> {f.arrival_time} | {f.duration} | via {via}{ac}")


def print_combos(name, out_flights, ret_flights):
    """Print best round-trip combos from two legs."""
    priced_out = [f for f in out_flights if f.price is not None]
    priced_ret = [f for f in ret_flights if f.price is not None]
    if not priced_out or not priced_ret:
        return

    out(f"\n{'=' * 70}")
    out(f"BEST {name} ONE-WAY COMBOS")
    out("=" * 70)

    cheapest_out = min(priced_out, key=lambda f: f.price)
    cheapest_ret = min(priced_ret, key=lambda f: f.price)
    total = cheapest_out.price + cheapest_ret.price
    out(f"Cheapest outbound: {cheapest_out.airline} - {format_price(cheapest_out)}")
    out(f"Cheapest return:   {cheapest_ret.airline} - {format_price(cheapest_ret)}")
    out(f"Combined total:    ${total:,.0f}")

    combos = []
    for o in sorted(priced_out, key=lambda f: f.price)[:10]:
        for r in sorted(priced_ret, key=lambda f: f.price)[:10]:
            combos.append((o, r, o.price + r.price))
    combos.sort(key=lambda x: x[2])

    out(f"\nTop 5 One-Way Combos ({name}):")
    seen = set()
    count = 0
    for o, r, total in combos:
        key = (o.ref or f"{o.airline}|{o.departure_time}", r.ref or f"{r.airline}|{r.departure_time}")
        if key in seen:
            continue
        seen.add(key)
        count += 1
        if count > 5:
            break
        o_via = ", ".join(l.code for l in o.layovers) if o.layovers else "direct"
        r_via = ", ".join(l.code for l in r.layovers) if r.layovers else "direct"
        o_fn = f" [{', '.join(o.flight_numbers)}]" if o.flight_numbers else ""
        r_fn = f" [{', '.join(r.flight_numbers)}]" if r.flight_numbers else ""
        out(f"  {count}. ${total:,.0f} total")
        out(f"     OUT: {o.airline}{o_fn} {format_price(o)} | {o.departure_date} {o.departure_time} -> {o.arrival_time} | {o.duration} | via {o_via}")
        out(f"     RET: {r.airline}{r_fn} {format_price(r)} | {r.departure_date} {r.departure_time} -> {r.arrival_time} | {r.duration} | via {r_via}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    out("=" * 70)
    out("COMPREHENSIVE FLIGHT SEARCH RESULTS")
    out(f"Generated: {date.today().isoformat()}")
    out("=" * 70)

    # Search all legs (parallel within each leg)
    leg_results = []
    for leg in LEGS:
        out(f"\n{'=' * 70}")
        out(f"{leg['name']}: {leg['label']}")
        out(f"Constraint: {leg['notes']}")
        out("=" * 70)

        flights = search_leg(leg)
        leg_results.append(flights)
        print_leg_summary(f"{leg['origin']}->{leg['dest']}", flights)

    # Combo analysis
    for combo in COMBOS:
        out_flights = leg_results[combo["outbound_leg"]]
        ret_flights = leg_results[combo["return_leg"]]
        print_combos(combo["name"], out_flights, ret_flights)

    # Grand total
    out(f"\n{'=' * 70}")
    out("GRAND TOTAL ESTIMATE (Cheapest per leg)")
    out("=" * 70)

    grand = 0
    all_priced = True
    for i, (leg, flights) in enumerate(zip(LEGS, leg_results)):
        priced = [f for f in flights if f.price is not None]
        if priced:
            c = min(priced, key=lambda f: f.price)
            fn = f" [{', '.join(c.flight_numbers)}]" if c.flight_numbers else ""
            out(f"Leg {i+1} {leg['origin']}->{leg['dest']}: {c.airline}{fn} - {format_price(c)}")
            grand += c.price
        else:
            out(f"Leg {i+1} {leg['origin']}->{leg['dest']}: NO PRICED FLIGHTS FOUND")
            all_priced = False

    if all_priced:
        out(f"\nGRAND TOTAL (all {len(LEGS)} one-way legs): ${grand:,.0f}")
    out("")

    # Write to file
    output_path = r"C:\Users\admin\.openclaw\workspace\skills\flight\search_results.txt"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(OUTPUT))
    print(f"\nResults saved to: {output_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
