"""CLI interface for flight search automation."""

import argparse
import io
import json
import sys
from datetime import date, timedelta

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from .parser import Flight
from .filters import apply_all_filters
from .formatter import (
    format_date_pair,
    format_oneway_date_results,
    format_rt_only,
    format_best_picks_summary,
    chunk_for_signal,
)
from .config import DEFAULT_RANGE_DAYS, MAX_RANGE_DAYS


def parse_date(s: str) -> date:
    """Parse YYYY-MM-DD date string."""
    return date.fromisoformat(s)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="flight_search",
        description="Search Google Flights for business class flights.",
    )
    p.add_argument("--from", dest="origin", required=True, help="Origin IATA code (e.g., BOM)")
    p.add_argument("--to", dest="dest", required=True, help="Destination IATA code (e.g., LAX)")

    # Date options
    p.add_argument("--date", help="Exact departure date (YYYY-MM-DD)")
    p.add_argument("--depart-after", help="Search dates after this date (YYYY-MM-DD)")
    p.add_argument("--depart-before", help="Search dates before this date (YYYY-MM-DD)")
    p.add_argument("--range-days", type=int, default=DEFAULT_RANGE_DAYS,
                    help=f"Number of dates to search (default: {DEFAULT_RANGE_DAYS})")

    # Return options
    p.add_argument("--return-after-days", type=int, help="Return N days after departure")
    p.add_argument("--return-date", help="Exact return date (YYYY-MM-DD)")
    p.add_argument("--one-way", action="store_true", help="One-way search only")
    p.add_argument("--compare", action="store_true",
                    help="Show both one-way combo AND round-trip fares side by side")

    # Filters
    p.add_argument("--max-stops", type=int, help="Maximum number of stops")
    p.add_argument("--max-layover", type=float, help="Maximum layover duration in hours")
    p.add_argument("--exclude-routing", help="Comma-separated IATA codes to exclude from routing (e.g., DXB,AUH,DOH)")

    # Backend
    p.add_argument("--backend", choices=["swoop", "browser", "sq"], default="swoop",
                    help="Search backend: swoop (Google Flights RPC), browser (agent-browser), sq (Singapore Airlines API)")

    # Output
    p.add_argument("--json", action="store_true", help="Output as JSON instead of formatted text")
    p.add_argument("--signal", action="store_true", help="Output chunked for Signal (max 2000 chars each)")
    p.add_argument("--no-cleanup", action="store_true", help="Skip pre-flight cleanup (browser backend only)")

    return p.parse_args(argv)


def compute_search_dates(args: argparse.Namespace) -> list[date]:
    """Compute the list of departure dates to search."""
    if args.date:
        return [parse_date(args.date)]

    if args.depart_after:
        start = parse_date(args.depart_after) + timedelta(days=1)
        num_days = min(args.range_days, MAX_RANGE_DAYS)
        if args.depart_before:
            end = parse_date(args.depart_before)
            num_days = min((end - start).days + 1, MAX_RANGE_DAYS)
        return [start + timedelta(days=i) for i in range(num_days)]

    print("Error: must specify --date or --depart-after", file=sys.stderr)
    sys.exit(1)


def compute_return_date(depart: date, args: argparse.Namespace) -> date | None:
    """Compute return date for a given departure date."""
    if args.one_way:
        return None
    if args.return_after_days:
        return depart + timedelta(days=args.return_after_days)
    if args.return_date:
        return parse_date(args.return_date)
    return None


def search_direction_swoop(
    origin: str,
    dest: str,
    dates: list[date],
    args: argparse.Namespace,
) -> dict[date, list[Flight]]:
    """Search one direction using swoop (parallel RPC, no browser)."""
    from .swoop_backend import search_swoop_parallel

    excluded = set()
    if args.exclude_routing:
        excluded = {c.strip().upper() for c in args.exclude_routing.split(",")}

    raw_results = search_swoop_parallel(origin, dest, dates, max_stops=args.max_stops)

    results = {}
    for d, flights in raw_results.items():
        flights = apply_all_filters(
            flights,
            max_stops=args.max_stops,
            max_layover_hours=args.max_layover,
            excluded_routing=excluded,
        )
        flights.sort(key=lambda f: f.price if f.price is not None else float("inf"))
        results[d] = flights

    return results


def search_direction_browser(
    origin: str,
    dest: str,
    dates: list[date],
    args: argparse.Namespace,
) -> dict[date, list[Flight]]:
    """Search one direction using agent-browser (Playwright/Chromium)."""
    from .browser import batch_search
    from .parser import parse_snapshot
    from .urls import build_url

    urls = []
    date_map = {}
    for i, d in enumerate(dates):
        session_name = f"d{i+1}"
        url = build_url(origin, dest, d, one_way=True)
        urls.append((session_name, url))
        date_map[session_name] = d

    snapshots = batch_search(urls)

    excluded = set()
    if args.exclude_routing:
        excluded = {c.strip().upper() for c in args.exclude_routing.split(",")}

    results = {}
    for session_name, snap_text in snapshots.items():
        d = date_map[session_name]
        flights = parse_snapshot(snap_text)
        flights = apply_all_filters(
            flights,
            max_stops=args.max_stops,
            max_layover_hours=args.max_layover,
            excluded_routing=excluded,
        )
        flights.sort(key=lambda f: f.price if f.price is not None else float("inf"))
        results[d] = flights

    return results


def search_roundtrip_swoop(
    origin: str,
    dest: str,
    date_pairs: list[tuple[date, date]],
    args: argparse.Namespace,
) -> dict[date, list[Flight]]:
    """Search round-trip fares using swoop (parallel RPC)."""
    from .swoop_backend import search_swoop_roundtrip_parallel

    excluded = set()
    if args.exclude_routing:
        excluded = {c.strip().upper() for c in args.exclude_routing.split(",")}

    raw_results = search_swoop_roundtrip_parallel(origin, dest, date_pairs, max_stops=args.max_stops)

    results = {}
    for d, flights in raw_results.items():
        flights = apply_all_filters(
            flights,
            max_stops=args.max_stops,
            max_layover_hours=args.max_layover,
            excluded_routing=excluded,
        )
        flights.sort(key=lambda f: f.price if f.price is not None else float("inf"))
        results[d] = flights

    return results


def search_direction_sq(
    origin: str,
    dest: str,
    dates: list[date],
    args: argparse.Namespace,
) -> dict[date, list[Flight]]:
    """Search one direction using Singapore Airlines browser scraping."""
    from .sq_backend import search_sq_parallel

    excluded = set()
    if args.exclude_routing:
        excluded = {c.strip().upper() for c in args.exclude_routing.split(",")}

    raw_results = search_sq_parallel(origin, dest, dates, max_stops=args.max_stops)

    results = {}
    for d, flights in raw_results.items():
        flights = apply_all_filters(
            flights,
            max_stops=args.max_stops,
            max_layover_hours=args.max_layover,
            excluded_routing=excluded,
        )
        flights.sort(key=lambda f: f.price if f.price is not None else float("inf"))
        results[d] = flights

    return results


def search_roundtrip_sq(
    origin: str,
    dest: str,
    date_pairs: list[tuple[date, date]],
    args: argparse.Namespace,
) -> dict[date, list[Flight]]:
    """Search round-trip fares using Singapore Airlines browser scraping."""
    from .sq_backend import search_sq_roundtrip_parallel

    excluded = set()
    if args.exclude_routing:
        excluded = {c.strip().upper() for c in args.exclude_routing.split(",")}

    raw_results = search_sq_roundtrip_parallel(origin, dest, date_pairs, max_stops=args.max_stops)

    results = {}
    for d, flights in raw_results.items():
        flights = apply_all_filters(
            flights,
            max_stops=args.max_stops,
            max_layover_hours=args.max_layover,
            excluded_routing=excluded,
        )
        flights.sort(key=lambda f: f.price if f.price is not None else float("inf"))
        results[d] = flights

    return results


def search_direction(
    origin: str,
    dest: str,
    dates: list[date],
    args: argparse.Namespace,
) -> dict[date, list[Flight]]:
    """Search one direction using the selected backend."""
    if args.backend == "swoop":
        return search_direction_swoop(origin, dest, dates, args)
    elif args.backend == "sq":
        return search_direction_sq(origin, dest, dates, args)
    else:
        return search_direction_browser(origin, dest, dates, args)


def flights_to_dicts(flights: list[Flight]) -> list[dict]:
    """Convert flights to JSON-serializable dicts."""
    return [
        {
            "airline": f.airline,
            "price": f.price,
            "price_currency": f.price_currency,
            "departure_time": f.departure_time,
            "arrival_time": f.arrival_time,
            "departure_airport": f.departure_airport,
            "arrival_airport": f.arrival_airport,
            "duration": f.duration,
            "stops": f.stops,
            "layovers": [
                {"duration": l.duration, "airport_code": l.code, "city": l.city}
                for l in f.layovers
            ],
            "price_unavailable": f.price_unavailable,
            "ref": f.ref,
        }
        for f in flights
    ]


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Step 1: Cleanup (browser backend only)
    if args.backend == "browser" and not args.no_cleanup:
        from .browser import cleanup
        print("Cleaning up stale sessions...", file=sys.stderr)
        cleanup()

    print(f"Backend: {args.backend}", file=sys.stderr)

    # Step 2: Compute dates
    depart_dates = compute_search_dates(args)
    print(f"Searching {len(depart_dates)} departure date(s): "
          f"{depart_dates[0].isoformat()} to {depart_dates[-1].isoformat()}",
          file=sys.stderr)

    # Determine search mode:
    #   --one-way              → one-way only
    #   return date (no flag)  → round-trip only
    #   --compare              → both one-way combo AND round-trip
    is_roundtrip = not args.one_way and (args.return_after_days or args.return_date)
    show_oneway = args.one_way or args.compare or not is_roundtrip
    show_rt = is_roundtrip and not args.one_way

    mode_label = "compare (OW + RT)" if args.compare else ("round-trip" if show_rt else "one-way")
    print(f"Mode: {mode_label}", file=sys.stderr)

    # Step 3: Search outbound (one-way) — needed for OW mode or compare mode
    outbound_results = {}
    if show_oneway:
        print(f"Searching outbound (one-way): {args.origin} → {args.dest}...", file=sys.stderr)
        outbound_results = search_direction(args.origin, args.dest, depart_dates, args)

    # Step 4: Search return (one-way) — needed for OW mode or compare mode
    return_results = {}
    depart_to_return = {}
    if not args.one_way:
        for d in depart_dates:
            ret = compute_return_date(d, args)
            if ret:
                depart_to_return[d] = ret

    if show_oneway and not args.one_way and depart_to_return:
        unique_return = sorted(set(depart_to_return.values()))
        print(f"Searching return (one-way): {args.dest} → {args.origin}...", file=sys.stderr)
        return_results_raw = search_direction(args.dest, args.origin, unique_return, args)
        for d in depart_dates:
            ret = depart_to_return.get(d)
            if ret and ret in return_results_raw:
                return_results[d] = return_results_raw[ret]

    # Step 4b: Search round-trip fares — needed for RT mode or compare mode
    rt_results = {}
    if show_rt and depart_to_return and args.backend in ("swoop", "sq"):
        date_pairs = [(d, depart_to_return[d]) for d in depart_dates if d in depart_to_return]
        print(f"Searching round-trip: {args.origin} ↔ {args.dest}...", file=sys.stderr)
        if args.backend == "sq":
            rt_results = search_roundtrip_sq(args.origin, args.dest, date_pairs, args)
        else:
            rt_results = search_roundtrip_swoop(args.origin, args.dest, date_pairs, args)

        # Also fetch return one-ways so we can show return routing for RT results
        if not return_results and depart_to_return:
            unique_return = sorted(set(depart_to_return.values()))
            print(f"Searching return routes: {args.dest} → {args.origin}...", file=sys.stderr)
            return_results_raw = search_direction(args.dest, args.origin, unique_return, args)
            for d in depart_dates:
                ret = depart_to_return.get(d)
                if ret and ret in return_results_raw:
                    return_results[d] = return_results_raw[ret]

    # Step 5: Format output
    if args.json:
        output = {}
        for d in depart_dates:
            key = d.isoformat()
            ret = compute_return_date(d, args)
            entry = {}
            if show_oneway:
                entry["outbound"] = flights_to_dicts(outbound_results.get(d, []))
            if ret and show_oneway and not args.one_way:
                entry["return"] = flights_to_dicts(return_results.get(d, []))
                entry["return_date"] = ret.isoformat()
            if show_rt and d in rt_results:
                entry["round_trip"] = flights_to_dicts(rt_results[d])
            output[key] = entry
        print(json.dumps(output, indent=2))
    else:
        all_text_parts = []
        all_outbound = []
        all_return = []

        for d in depart_dates:
            ret_date = compute_return_date(d, args)

            if args.one_way:
                # One-way only
                out_flights = outbound_results.get(d, [])
                all_outbound.extend(out_flights)
                date_label = f"{args.origin} → {args.dest}: {d.strftime('%B %d')}"
                text = format_oneway_date_results(date_label, "", out_flights)

            elif args.compare:
                # Both one-way combo and round-trip
                out_flights = outbound_results.get(d, [])
                all_outbound.extend(out_flights)
                ret_flights = return_results.get(d, [])
                all_return.extend(ret_flights)
                rt_flights = rt_results.get(d, [])
                date_label = f"{d.strftime('%B %d')} → {ret_date.strftime('%B %d')}" if ret_date else d.strftime('%B %d')
                text = format_date_pair(
                    date_label=date_label,
                    outbound_label=f"OUTBOUND: {args.origin} → {args.dest} ({d.strftime('%b %d')})",
                    outbound_flights=out_flights,
                    return_label=f"RETURN: {args.dest} → {args.origin} ({ret_date.strftime('%b %d')})" if ret_date else "",
                    return_flights=ret_flights,
                    rt_flights=rt_flights if rt_flights else None,
                    rt_return_date=ret_date.strftime('%b %d') if ret_date and rt_flights else "",
                )

            elif is_roundtrip:
                # Round-trip only
                rt_flights = rt_results.get(d, [])
                ret_flights = return_results.get(d, [])
                date_label = f"{args.origin} ↔ {args.dest}: {d.strftime('%B %d')} → {ret_date.strftime('%B %d')}" if ret_date else d.strftime('%B %d')
                text = format_rt_only(
                    date_label=date_label,
                    rt_flights=rt_flights,
                    return_flights=ret_flights,
                    return_date=ret_date.isoformat() if ret_date else "",
                )

            else:
                out_flights = outbound_results.get(d, [])
                all_outbound.extend(out_flights)
                date_label = f"{args.origin} → {args.dest}: {d.strftime('%B %d')}"
                text = format_oneway_date_results(date_label, "", out_flights)

            all_text_parts.append(text)

        # Best picks summary
        if all_outbound or all_return:
            all_text_parts.append(format_best_picks_summary(all_outbound, all_return))

        full_text = "\n".join(all_text_parts)

        if args.signal:
            chunks = chunk_for_signal(full_text)
            for i, chunk in enumerate(chunks, 1):
                print(f"--- Signal Message {i}/{len(chunks)} ---")
                print(chunk)
                print()
        else:
            print(full_text)

    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
