---
name: google-flights
description: Search Google Flights for flight prices and schedules using browser automation. Use when user asks to search flights, find airfare, compare prices, check flight availability, or look up routes. Triggers include "search flights", "find flights", "how much is a flight", "flights from X to Y", "cheapest flight", "flight prices", "airfare", "flight schedule", "nonstop flights", "when should I fly".
allowed-tools: Bash(agent-browser:*), Bash(python:*)
---

# Google Flights Search

Search Google Flights via agent-browser to find flight prices, schedules, and availability.

## When to Use

- User asks to search/find/compare flights or airfare
- User wants to know flight prices between cities
- User asks about flight schedules or availability
- User wants to find the cheapest flight for specific dates

## When NOT to Use

- **Completing purchases**: This skill finds flights and extracts booking links, but do not attempt to complete a purchase on a booking site.
- **Hotels/rental cars**: Use other tools for non-flight travel searches.
- **Historical price data**: Google Flights shows current prices, not historical.

---

## MANDATORY RULES (NON-NEGOTIABLE)

These rules are BINDING. They override ALL defaults. Every single rule MUST be followed — no exceptions, no skipping, no "I'll check later." Violations are not acceptable.

### Banned Airlines

These airlines are BANNED. Filter them out of ALL results. Never show them in output, even if Google Flights returns them:

- **Air India**
- **Ethiopian**
- **Kenya Airways**
- **Royal Jordanian**
- **Thai Airways**

If these are the only results for a route, report "no suitable flights found (excluding blocked airlines)."

### Cabin Class: Business ONLY

- **NEVER search or display economy class.**
- Do not open economy sessions.
- Do not include economy prices in output.
- Do not show economy comparisons or deltas.
- Business class is the ONLY cabin to search. This saves tokens, sessions, and time.
- Every search URL MUST include `+business+class`.
- The only exception is if the user explicitly says "search economy" — otherwise, business always.

### Ticketing Strategy

- Even if a trip is round-trip, **always check two separate one-way tickets** as options.
- If a trip is complex (multi-leg, open-jaw), check one-way AND also do multi-city checks.
- Compare combined one-way totals against round-trip prices when both are available.

| User request | Searches to run |
|-------------|----------------|
| Round-trip A↔B | One-way A→B + one-way B→A (always) + RT A↔B (optional comparison) |
| One-way A→B | One-way A→B |
| Multi-leg A→B→C→A | Individual one-ways for each leg + multi-city search, compare totals |

### Date Range Expansion

When the user specifies a flexible or open-ended departure window (e.g., "anytime after April 6", "leaving between May 1-10", "early June"), search EVERY individual eligible departure date separately. Each date gets its own search with the corresponding return date calculated from the user's duration requirement (e.g., "returning after 30 days" means departure + 30 days). Present results grouped by departure date so the user can compare across dates.

- If user says "after DATE", search DATE+1 through DATE+7 (7 consecutive dates by default, up to 14 max).
- If user says "between DATE1 and DATE2", search every date in that range.
- If user says "early/mid/late MONTH", search the corresponding ~10-day window.
- If user says "anytime in MONTH", search representative dates spread across the month (1st, 5th, 10th, 15th, 20th, 25th).
- Return dates are calculated per the user's duration (e.g., +30 days from each departure).
- Each departure date is a separate search — do NOT collapse them into a single search.
- Group output by departure date: `--- April 7 → May 7 ---`, `--- April 8 → May 8 ---`, etc.
- The user can override the range by specifying exact dates or saying "just check April 10".
- Respect the VM session limit (4-5 max). Batch dates across multiple rounds if needed.

### Enforcement Checkpoints

1. Apply banned airlines filter → AFTER collecting results, BEFORE presenting to user
2. Apply cabin minimum → WHEN constructing search URLs (add `+business+class`)
3. Apply ticketing strategy → WHEN planning which searches to run
4. Apply date range expansion → WHEN user gives flexible/open dates

---

## Step 1 — Pre-Flight Cleanup (MUST run before every search)

**Kill all stale browser sessions and processes.** This ensures a clean slate — no leftover sessions from crashed runs, no orphaned Chromium processes eating memory.

```bash
# Step 1: List and close any existing agent-browser sessions
agent-browser session list 2>/dev/null | while read -r line; do
  session_id=$(echo "$line" | awk '{print $1}')
  [ -n "$session_id" ] && agent-browser --session "$session_id" close 2>/dev/null &
done
wait

# Step 2: Force-kill any orphaned Chromium/Chrome processes left by Playwright
MSYS_NO_PATHCONV=1 taskkill /IM chrome.exe /F 2>/dev/null
MSYS_NO_PATHCONV=1 taskkill /IM chromium.exe /F 2>/dev/null
```

**Rules:**
- This cleanup runs at the **start of every skill invocation**, not just when errors are suspected
- Both steps are required — `agent-browser session list` catches tracked sessions, `taskkill` catches orphaned processes that the session manager lost track of
- `MSYS_NO_PATHCONV=1` prevents Git Bash from mangling the `/IM` and `/F` flags
- After cleanup, wait 2 seconds before opening new sessions to let ports free up
- Only after cleanup passes should you proceed to begin searches

---

## Step 2 — Execute Searches via Python Module (PRIMARY METHOD)

The `flight_search` Python module automates the entire search pipeline. It uses **three backends**:

1. **`swoop` (default)** — Calls Google Flights RPC endpoints directly. No browser needed. Instant results, zero Chromium overhead. Uses TLS fingerprinting to match real browser sessions.
2. **`sq`** — Scrapes singaporeair.com directly via agent-browser (headed Chromium with Akamai bypass). No API key needed. Returns SQ-only flights with accurate pricing, aircraft types, segment details, and layover info. Prices auto-converted to USD. Slower than swoop (~30-60s per search) but gives real SQ pricing that Google Flights often shows as "price unavailable".
3. **`browser`** — Falls back to `agent-browser` (Playwright/Chromium) for browser-based snapshot parsing. Use when swoop fails or for tasks requiring browser interaction (booking links, multi-city).

```bash
# One-way search (swoop backend, default)
python -m flight_search --from BOM --to LAX --date 2026-04-07 --one-way --max-stops 1 --max-layover 6.5 --exclude-routing DXB,AUH,DOH

# Round-trip with date range (searches Apr 7-13, return 30 days after each)
python -m flight_search --from BOM --to LAX --depart-after 2026-04-06 --return-after-days 30 --max-stops 1 --max-layover 6.5 --exclude-routing DXB,AUH,DOH

# Exact date range
python -m flight_search --from BOM --to LAX --depart-after 2026-04-06 --depart-before 2026-04-13 --return-after-days 30

# Singapore Airlines backend (SQ-only results)
python -m flight_search --from SIN --to LHR --date 2026-04-07 --one-way --backend sq

# Force browser backend (fallback)
python -m flight_search --from BOM --to LAX --date 2026-04-07 --one-way --backend browser

# JSON output
python -m flight_search --from BOM --to LAX --date 2026-04-07 --one-way --json

# Signal-chunked output (≤2000 chars per message)
python -m flight_search --from BOM --to LAX --depart-after 2026-04-06 --return-after-days 30 --signal
```

### CLI Options

| Flag | Description |
|------|-------------|
| `--from` / `--to` | Origin/destination IATA codes (required) |
| `--date` | Exact departure date (YYYY-MM-DD) |
| `--depart-after` | Search dates after this date |
| `--depart-before` | Search dates before this date |
| `--range-days` | Number of dates to search (default: 7, max: 14) |
| `--return-after-days` | Return N days after departure |
| `--return-date` | Exact return date (YYYY-MM-DD) |
| `--one-way` | One-way search only |
| `--max-stops` | Maximum number of stops |
| `--max-layover` | Maximum layover duration in hours |
| `--exclude-routing` | Comma-separated IATA codes to exclude from routing |
| `--backend` | `swoop` (default, Google Flights RPC), `sq` (Singapore Airlines API), or `browser` (agent-browser fallback) |
| `--json` | JSON output |
| `--signal` | Chunked output for Signal (≤2000 chars each) |
| `--no-cleanup` | Skip pre-flight browser cleanup (browser backend only) |

### What the module handles automatically:
- **Swoop backend**: Direct Google Flights RPC calls — no browser, no Chromium, no sessions. Returns real-time prices with flight numbers, aircraft types, layover details, and IATA codes natively.
- **SQ backend**: Browser-based scraping of singaporeair.com — uses headed Chromium with `AutomationControlled` disabled to bypass Akamai. Extracts flight data via DOM parsing (JavaScript eval). Prices auto-converted to USD. Sequential searches (~30-60s each).
- **Browser backend**: Pre-flight cleanup, URL construction, batch browser sessions (max 4 parallel), snapshot parsing.
- All backends: Banned airline filtering, stop/layover/routing filters, formatted output (outbound + return grouped by date pair, best combos, best picks), Signal message chunking.

### When to use Singapore Airlines backend (`--backend sq`):
- User specifically wants SQ flights (SQ often shows "price unavailable" on Google Flights)
- Routes through SIN where SQ is a primary carrier
- SQ pricing not available via swoop/Google Flights
- Combines with swoop results: run both backends, merge results for complete picture
- No API key needed — scrapes the website directly with Akamai bypass
- Prices are auto-converted to USD from whatever local currency SQ shows

### When to fall back to browser backend (`--backend browser`):
- Swoop RPC calls fail (rate-limited, endpoint changed)
- Booking link extraction (click specific flight → get provider URLs)
- Interactive fallback when swoop can't handle the query

### When to use agent-browser directly (manual commands):
- Multi-city searches (3+ legs) — though swoop also supports these via `search_legs()`
- Booking link extraction requiring browser clicks
- Any task requiring specific element interaction in the page

---

## Manual agent-browser Reference (Fallback)

The sections below document the manual `agent-browser` workflow. Use this when the Python module cannot handle the task (multi-city, booking links, interactive fallback).

## Data Fetching Rule

**Always use `agent-browser` (Playwright/Chromium) to fetch flight data.** Google Flights is a JavaScript-heavy SPA that requires a real browser to render results. Never use `curl`, `wget`, `fetch`, raw HTTP requests, or scraping libraries — they will not return usable data.

## Singapore Airlines on Google Flights

**Singapore Airlines** has partial fare availability on Google Flights:

- **Priced SQ flights** — appear with normal prices and work perfectly. Clicking "Select flight" shows "Book with Singapore Airlines" with a direct booking link. These are handled the same as any other airline.
- **"Price unavailable" SQ flights** — show schedule/itinerary details but no price. Clicking "Select flight" shows "We can't find booking options for this itinerary." These are dead ends.

**Pattern**: Longer-layover SQ routings (e.g., 13h SIN layover) tend to have prices. Short-layover routings (e.g., 55min SIN) often show "price unavailable."

### SQ Website: BLOCKED (Akamai + reCAPTCHA)

singaporeair.com has **two layers** of bot detection that cannot be bypassed:
1. **Akamai Bot Manager** — blocks standard headless Chromium on page load
2. **reCAPTCHA** — triggers on the actual flight search, even with stealth browser flags

Stealth mode bypasses Akamai but the search action itself triggers reCAPTCHA. **Do not attempt SQ website searches.**

### SQ Handling in Results

When presenting business/first class results on SIN-routed flights:
1. Include any SQ flights that show prices on Google Flights (these have full booking options)
2. Mention SQ flights with "price unavailable" with schedule info only
3. Note: "Some SQ fares unavailable on Google Flights. For complete SQ pricing, check singaporeair.com directly in a regular browser."

## Search Order

1. **Google Flights URL fast path** — preferred, 3 commands
2. **Google Flights interactive** — fallback for multi-city, or when URL fails

## VM Resource Constraint: Max 4–5 Browser Sessions

This agent runs on a VM with limited resources. **Never have more than 4–5 browser sessions open at the same time.**

### Batch-and-Collect Pattern

When a search requires multiple browser sessions (e.g., multiple dates/routes), work in batches:

1. **Open** up to 4 sessions in parallel
2. **Fetch** — wait for pages to load, snapshot/extract data
3. **Collect** — store the extracted data (prices, airlines, times) in memory
4. **Close** all sessions in that batch
5. **Proceed** — open the next batch if more searches are needed
6. **Close** — ensure zero open sessions remain
7. **Report** — compile the final results and send to Signal as chunked messages (≤2000 chars each)

### Example: Business Class for 4 Dates (4 searches per batch)

```bash
# ── Batch 1: Apr 7 + Apr 8 + Apr 9 + Apr 10 = 4 sessions ──
agent-browser --session d1 open "https://www.google.com/travel/flights?q=Flights+from+BOM+to+LAX+on+2026-04-07+one+way+business+class" &
agent-browser --session d2 open "https://www.google.com/travel/flights?q=Flights+from+BOM+to+LAX+on+2026-04-08+one+way+business+class" &
agent-browser --session d3 open "https://www.google.com/travel/flights?q=Flights+from+BOM+to+LAX+on+2026-04-09+one+way+business+class" &
agent-browser --session d4 open "https://www.google.com/travel/flights?q=Flights+from+BOM+to+LAX+on+2026-04-10+one+way+business+class" &
wait

agent-browser --session d1 wait --load networkidle &
agent-browser --session d2 wait --load networkidle &
agent-browser --session d3 wait --load networkidle &
agent-browser --session d4 wait --load networkidle &
wait

agent-browser --session d1 snapshot -i
agent-browser --session d2 snapshot -i
agent-browser --session d3 snapshot -i
agent-browser --session d4 snapshot -i

# Close ALL sessions in this batch before opening more
agent-browser --session d1 close &
agent-browser --session d2 close &
agent-browser --session d3 close &
agent-browser --session d4 close &
wait

# ── Batch 2: Apr 11 + Apr 12 + Apr 13 = 3 sessions ──
# ... same pattern ...

# ── Final: Compile all collected data and report to Signal ──
```

### Date Range Expansion — Execution Pattern

For a 7-date range with one-way business class outbound + return, that's **14 searches** (7 outbound + 7 return). Batch them:

```
Batch 1: Apr 7 OW out + Apr 8 OW out + Apr 9 OW out + Apr 10 OW out  (4 sessions)
Batch 2: Apr 11 OW out + Apr 12 OW out + Apr 13 OW out               (3 sessions)
Batch 3: May 7 OW ret + May 8 OW ret + May 9 OW ret + May 10 OW ret  (4 sessions)
Batch 4: May 11 OW ret + May 12 OW ret + May 13 OW ret               (3 sessions)
```

Each batch: open → wait networkidle → snapshot → collect data → close all → next batch.

Then group results by departure→return date pair in the final output.

### Session Counting Rules

| Scenario | Sessions | Within limit? |
|----------|----------|---------------|
| Business class, 1 date | 1 | Yes |
| Business class, 4 dates | 4 | Yes — at limit |
| Business class, 7 dates | 7 | **No** — split into 2 batches (4 + 3) |
| Booking link extraction | 1 | Yes |

**Key rule**: Always close finished sessions before opening new ones. Never let sessions pile up.

### Speed Optimization

To minimize total search time on the VM:

1. **Always parallelize opens** — launch all sessions in a batch with `&` + `wait`. Opening 4 browsers sequentially takes 4x longer than parallel.
2. **Parallelize waits** — `wait --load networkidle` for all sessions in parallel, not one at a time.
3. **Snapshot sequentially** — snapshots must be sequential (each returns data you need to process), but they're fast (~1s each).
4. **Parallelize closes** — background all close commands with `&` + `wait`.
5. **Use URL fast path over interactive** — 3 commands vs 15+. The interactive workflow is 5–10x slower.
6. **ALWAYS use `snapshot -i` — NEVER use `get text body`** — On Google Flights, `get text body` returns 160KB+ of raw JavaScript source code, NOT the rendered flight data. `snapshot -i` returns clean structured interactive elements. There is no valid use case for `get text body` on Google Flights.
7. **Never search economy** — business is the only cabin. Do not waste sessions on economy searches.

**Typical timing (fast path, 1 date, business):**
- Open 1 browser: ~2s
- Wait for networkidle: ~5–8s
- Snapshot: ~1s
- Close: ~1s
- **Total: ~9–12s**

## Session Convention

- **Single date search**: `--session flights`
- **Multi-date batches**: `--session d1`, `--session d2`, `--session d3`, `--session d4`
- **Interactive fallback**: `--session flights`
- **Booking link extraction**: `--session booking`

**All sessions are short-lived.** Open → fetch → collect → close. Never hold sessions open waiting for user replies.

## Fast Path: URL-Based Search (Preferred)

Construct a URL with a natural language `?q=` parameter. Loads results directly — **3 commands total**.

### URL Template

```
https://www.google.com/travel/flights?q=Flights+from+{ORIGIN}+to+{DEST}+on+{DATE}[+returning+{DATE}][+one+way]+business+class[+N+passengers]
```

**Note**: `+business+class` is ALWAYS included. There is no template without it.

### Default: Business Class (the only option)

```bash
# Business class search (default and only behavior)
agent-browser --session flights open "https://www.google.com/travel/flights?q=Flights+from+BKK+to+NRT+on+2026-03-20+returning+2026-03-27+business+class"
agent-browser --session flights wait --load networkidle
agent-browser --session flights snapshot -i
agent-browser --session flights close
```

Then present results in **compact list format** (see Output Format section below).

### One Way

```bash
agent-browser --session flights open "https://www.google.com/travel/flights?q=Flights+from+LAX+to+LHR+on+2026-04-15+one+way+business+class"
agent-browser --session flights wait --load networkidle
agent-browser --session flights snapshot -i
agent-browser --session flights close
```

### First Class / Multiple Passengers

```bash
agent-browser --session flights open "https://www.google.com/travel/flights?q=Flights+from+JFK+to+CDG+on+2026-06-01+returning+2026-06-15+first+class+2+adults+1+child"
agent-browser --session flights wait --load networkidle
agent-browser --session flights snapshot -i
agent-browser --session flights close
```

### What Works via URL

| Feature | URL syntax | Status |
|---------|-----------|--------|
| Round trip | `+returning+YYYY-MM-DD` | Works |
| One way | `+one+way` | Works |
| Business class | `+business+class` | Works |
| First class | `+first+class` | Works |
| N passengers (adults) | `+N+passengers` | Works |
| Adults + children | `+2+adults+1+child` | Works |
| IATA codes | `BKK`, `NRT`, `LAX` | Works |
| City names | `Bangkok`, `Tokyo` | Works |
| Dates as YYYY-MM-DD | `2026-03-20` | Works (best) |
| Natural dates | `March+20` | Works |
| **Multi-city** | N/A | **Fails** |

### What Requires Interactive Fallback

- **Multi-city** trips (3+ legs)
- **Infant passengers** (seat vs lap distinction)
- **URL didn't load results** (consent banner, CAPTCHA, locale issue)

### Calendar Price Lazy-Loading

Prices on calendar dates **lazy-load on hover** — they won't appear in the initial snapshot, especially for far-future months. When using the interactive fallback or date grid, hover over dates in the target month to trigger the price load before snapshotting:

```bash
# After navigating to the target month, hover a few dates to trigger price load
agent-browser --session flights hover @eN   # Hover first date in target month
agent-browser --session flights wait 1500
agent-browser --session flights hover @eN   # Hover another date nearby
agent-browser --session flights wait 1500
agent-browser --session flights snapshot -i # Prices now populated
```

### Calendar Prices Are Unreliable for Business Class

**Critical**: Google Flights calendar prices for business class searches are **misleading**. The calendar shows the lowest price for each date, but these often include **mixed-cabin fares** (e.g., economy on one leg, business on the other). There is no option to filter out mixed-class results in the calendar view.

**The only way to get pure business class prices** is to complete the search (Done → Search) and look at the actual flight results, which show the cabin class per flight leg.

**Rule**: Never quote business class prices from the calendar/date picker. Always run the full search and extract prices from the results page where cabin class per leg is visible.

### Reading Results from Snapshot

Each flight appears as a `link` element with a full description:

```
link "From 20508 Thai baht round trip total. Nonstop flight with Air Japan.
     Leaves Suvarnabhumi Airport at 12:10 AM on Friday, March 20 and arrives
     at Narita International Airport at 8:15 AM on Friday, March 20.
     Total duration 6 hr 5 min. Select flight"
```

Parse business class snapshots into the **compact list format**. The snapshot link text contains: airline, departure/arrival times, duration, stops, layover airport, and price. Extract ALL of these — especially **layover duration and airport code** for connecting flights:

```
1. JAL — Nonstop · 5h 55m
   8:05 AM → 4:00 PM · THB 65,915

2. Cathay Pacific — 1 stop (HKG) · 21h 35m
   1:40 AM → 10:45 AM · $6,956
   Layover: 3h 30m HKG

3. Lufthansa — 1 stop (FRA) · 23h
   2:40 AM → 1:10 PM · $9,153
   Layover: 2h 35m FRA
```

**Layover info is mandatory** — if a flight has a stop, always extract and show the layover duration and airport. The user filters by layover constraints (e.g., "no layover exceeding 6.5 hours") so this data must always be present.

Include "Best"/"Cheapest" labels from Google when present. Budget carriers without business class (ZIPAIR, Air Japan, etc.) will not appear in business class results — do not include them.

## Booking Options Handoff

After presenting the results table, **always offer booking links**: "Want booking links for any of these? Just say which one."

When the user picks a flight, extract booking options by clicking the flight's `link` element in the snapshot. Google Flights shows a panel with booking providers (airlines, OTAs) each with a price and a "Continue" link to the booking site.

### Workflow

```bash
# User picks flight #N — re-open a fresh session with the original search URL
agent-browser --session booking open "https://www.google.com/travel/flights?q=Flights+from+BKK+to+NRT+on+2026-03-20+returning+2026-03-27+business+class"
agent-browser --session booking wait --load networkidle
agent-browser --session booking snapshot -i

# Click the matching flight from the results
agent-browser --session booking click @eN
agent-browser --session booking wait 3000
agent-browser --session booking snapshot -i

# Extract booking links, then close immediately
# ... (extract data) ...
agent-browser --session booking close
```

The booking panel snapshot will show `link` elements like:

```
link "Book with Emirates THB 28,960" → href="https://..."
link "Book with Booking.com THB 29,512" → href="https://..."
link "Book with Teaflight THB 28,171" → href="https://..."
```

Extract the provider name, price, and `href` URL from each link.

### Booking Output Format

```
Booking Options for JAL BKK→NRT (5h 55m, Nonstop)

1. Emirates — THB 28,960
   https://...

2. Booking.com — THB 29,512
   https://...

3. Teaflight — THB 28,171
   https://...
```

### Notes

- **Session lifecycle**: Close ALL sessions immediately after collecting data — never hold sessions open waiting for the user to reply. On Signal, users may take minutes to respond and stale browser sessions waste VM resources.
- **If user requests booking links**: Open a fresh session, navigate back to the flight results using the URL fast path, click the flight, and extract booking providers. This costs 1 session for a few seconds — much better than holding a session open indefinitely.
- **If booking panel fails to load**: Re-snapshot and wait longer before retrying.

## Interactive Workflow (Fallback)

Use for multi-city or when the URL path fails.

### Open and Snapshot

```bash
agent-browser --session flights open "https://www.google.com/travel/flights"
agent-browser --session flights wait 3000
agent-browser --session flights snapshot -i
```

If a consent banner appears, click "Accept all" or "Reject all" first.

### Set Trip Type (if not Round Trip)

```bash
agent-browser --session flights click @eN   # Trip type combobox ("Round trip")
agent-browser --session flights snapshot -i
agent-browser --session flights click @eN   # "One way" or "Multi-city"
agent-browser --session flights wait 1000
agent-browser --session flights snapshot -i
```

### Set Cabin Class / Passengers (if non-default)

**Cabin class** (always set to Business or higher):
```bash
agent-browser --session flights click @eN   # Cabin class combobox
agent-browser --session flights snapshot -i
agent-browser --session flights click @eN   # Select "Business" class
agent-browser --session flights wait 1000
agent-browser --session flights snapshot -i
```

**Passengers:**
```bash
agent-browser --session flights click @eN   # Passengers button
agent-browser --session flights snapshot -i
agent-browser --session flights click @eN   # "+" for Adults/Children/Infants
agent-browser --session flights snapshot -i
agent-browser --session flights click @eN   # "Done"
agent-browser --session flights wait 1000
agent-browser --session flights snapshot -i
```

### Enter Airport (Origin or Destination)

```bash
agent-browser --session flights click @eN   # Combobox field
agent-browser --session flights wait 1000
agent-browser --session flights snapshot -i
agent-browser --session flights fill @eN "BKK"
agent-browser --session flights wait 2000   # CRITICAL: wait for autocomplete
agent-browser --session flights snapshot -i
agent-browser --session flights click @eN   # Click suggestion (NEVER press Enter)
agent-browser --session flights wait 1000
agent-browser --session flights snapshot -i
```

### Set Dates

```bash
agent-browser --session flights click @eN   # Date textbox
agent-browser --session flights wait 1000
agent-browser --session flights snapshot -i
# Calendar shows dates as buttons: "Friday, March 20, 2026"

# NOTE: Prices on calendar dates lazy-load on hover. If you need prices
# for a specific month (e.g., navigated to May), hover over a few dates
# first to trigger the load:
agent-browser --session flights hover @eN   # Hover a date in the target month
agent-browser --session flights wait 1500   # Wait for prices to lazy-load
agent-browser --session flights snapshot -i # Now prices should be populated

agent-browser --session flights click @eN   # Click target date
agent-browser --session flights wait 500
agent-browser --session flights snapshot -i
# Click "Done" to close calendar
agent-browser --session flights click @eN   # "Done" button
agent-browser --session flights wait 1000
agent-browser --session flights snapshot -i
```

### Search

**"Done" only closes the calendar. You MUST click "Search" separately.**

```bash
agent-browser --session flights click @eN   # "Search" button
agent-browser --session flights wait --load networkidle
agent-browser --session flights snapshot -i
# Close session after collecting data — re-open fresh if booking links needed later
```

### Multi-City Specifics

After selecting "Multi-city" trip type, the form shows one row per leg:

- Each leg has: origin combobox, destination combobox, departure date textbox
- **Origins auto-fill** from the previous leg's destination
- Click "Add flight" to add more legs (default: 2 legs shown)
- Click "Remove flight from X to Y" buttons to remove legs
- Results show flights for the **first leg**, with prices reflecting the **total multi-city cost**

Fill each leg's destination + date in order, then click "Search".

## Output Format

**Always use compact list format** — never markdown tables. Output is sent via Signal gateway where tables render as broken text.

### Signal Message Length Limit

Signal has a ~2000-character limit per message. If the full results exceed this:

1. **Split by flight count** — send 4–5 flights per message
2. **Send messages sequentially** — first batch, then second batch
3. **Always end the last message** with the booking links prompt

Example for 8 flights:
- Message 1: Flights 1–4 + "More results coming..."
- Message 2: Flights 5–8 + "Want booking links for any of these? Just say which one."

For booking options, each provider + URL is one line — these are short and usually fit in a single message.

### Business class (default and only format)

**CRITICAL: Always show BOTH outbound AND return flight details.** When searching one-way pairs (outbound + return), present them together so the user sees the full picture — routing, layover, times, and price for BOTH directions.

#### One-way search results (single direction)

```
--- BOM → LAX: April 7 ---
1. Cathay Pacific — 1 stop (HKG) · 21h 35m
   1:40 AM → 10:45 AM · $6,956
   Layover: 3h 30m HKG

2. Lufthansa — 1 stop (FRA) · 23h
   2:40 AM → 1:10 PM · $9,153
   Layover: 2h 35m FRA

--- BOM → LAX: April 8 ---
1. Cathay Pacific — 1 stop (HKG) · 21h 35m
   1:40 AM → 10:45 AM · $6,956
   Layover: 3h 30m HKG
...
```

#### Combined outbound + return results (paired by date)

When presenting the final combined results for a round-trip query, pair outbound and return together:

```
=== April 7 → May 7 ===

OUTBOUND: BOM → LAX (Apr 7)
1. Cathay Pacific — 1 stop (HKG) · 21h 35m
   1:40 AM → 10:45 AM · $6,956
   Layover: 3h 30m HKG

2. Lufthansa — 1 stop (FRA) · 23h
   2:40 AM → 1:10 PM · $9,153
   Layover: 2h 35m FRA

RETURN: LAX → BOM (May 7)
1. Cathay Pacific — 1 stop (HKG) · 23h 25m
   11:15 PM → 10:40 AM+2 · $4,644
   Layover: 4h 10m HKG

2. Turkish Airlines — 1 stop (IST) · 20h 35m
   7:25 PM → 5:00 AM+2 · $6,240
   Layover: 2h 50m IST

BEST COMBO: Cathay out $6,956 + Cathay ret $4,644 = $11,600

=== April 8 → May 8 ===
...
```

### Format rules

- **Always show BOTH outbound and return flights** — never show outbound only
- Include **layover details** for every connecting flight: duration + airport code (e.g., "Layover: 3h 30m HKG")
- Include **routing** — which airports the flight connects through
- One flight per numbered block, blank line between flights
- Line 1: Airline — Stops (via cities) · Total duration
- Line 2: Departure time → Arrival time · Price
- Line 3: Layover details (duration + airport) for each stop
- Group by departure date when multiple dates are searched
- Show BEST COMBO (cheapest outbound + cheapest return total) per date pair
- No economy prices, no economy comparisons — business only
- No code blocks around the flight list — plain text reads best
- Keep the "Best value" recommendation as a plain text paragraph after all dates

## Key Rules

| Rule | Why |
|------|-----|
| Always use `agent-browser` (Playwright/Chromium) | Flight sites are JS SPAs — curl/fetch/HTTP won't work |
| Prefer URL fast path | 3 commands vs 15+ interactive |
| Prefer one-way searches for business class | RT biz searches get rate-limited — combine one-way results instead |
| `wait --load networkidle` | Smarter than fixed `wait 5000` — returns when network settles |
| Use `fill` not `type` for airports | Clears existing text first |
| Wait 2s after typing airport codes | Autocomplete needs API roundtrip |
| Always CLICK suggestions, never Enter | Enter is unreliable for autocomplete |
| Re-snapshot after every interaction | DOM changes invalidate refs |
| Hover dates before reading prices | Calendar prices lazy-load on hover — won't appear in initial snapshot |
| Never trust calendar prices for business class | Calendar shows mixed-cabin fares — only actual search results show true business prices |
| "Done" ≠ Search | Calendar Done only closes picker |
| After presenting results, offer booking links | Users almost always want to book — prompt them |
| Flag Singapore Airlines "price unavailable" flights | Some SQ flights are priced (bookable), others show "price unavailable" — note the gap and suggest singaporeair.com |
| Close ALL sessions after collecting data | Never hold sessions open — re-open fresh if user asks for booking links later |
| Max 4–5 browser sessions at once | VM has limited resources — batch, collect, close, then proceed |
| Always close sessions before opening new batch | Never let sessions pile up — collect data first, then close all |
| Collect all data, close all sessions, then report | Gather everything, close browsers, then send chunked messages to Signal |
| Split long results into ≤2000-char messages | Signal has a message length limit — chunk by 4–5 flights per message |
| **Pre-flight cleanup: kill all stale sessions + orphaned Chromium** | MUST run at the start of every skill invocation — ensures clean slate, no memory leaks |
| **Filter out all banned airlines** | Air India, Ethiopian, Kenya Airways, Royal Jordanian, Thai Airways — never show these |
| **NEVER search economy** | Business is the only cabin. Do not open economy sessions, do not show economy prices. No exceptions. |
| **Always check one-way tickets** | Even for RT requests, run separate one-ways and compare totals |
| **Complex trips: one-way + multi-city** | Run both and compare to find the best deal |
| **Expand flexible date ranges** | "after April 6" = search Apr 7, 8, 9, 10, 11, 12, 13 individually — never collapse to one date |
| **Group output by departure date** | Each date pair gets its own section: `=== April 7 → May 7 ===` |
| **ALWAYS show both outbound AND return flights** | Never show only departure — user needs to see return routing, layover, times, and price too |
| **Include layover details on every connecting flight** | Duration + airport code (e.g., "Layover: 3h 30m HKG") — user filters by layover constraints |
| **Show BEST COMBO per date pair** | Cheapest outbound + cheapest return = total for that date pair |

## Anti-Bot Fallback Pattern

Some airline websites (e.g., Singapore Airlines) use bot detection (Akamai, Cloudflare) that blocks standard headless Chromium. Symptoms: fake error/maintenance pages, "Oops! Something went wrong", reference codes like `0.130ed217.*`.

**Detection**: If a site returns an error page but `curl` shows it's actually up (HTTP 200 with real content), it's bot detection, not real maintenance.

**Fallback**: Switch to stealth browser mode with these flags:
```bash
agent-browser --session <name> open "<url>" \
  --headed \
  --profile "<persistent-profile-path>" \
  --args "--disable-blink-features=AutomationControlled" \
  --user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
```

**Rules:**
- Close ALL sessions before switching to stealth (daemon caches launch args)
- Use `open` not `navigate` with `--profile`
- After stealth session, close it before launching normal sessions (daemon restarts with default args)
- Stealth sessions count toward the 4-session VM limit

## Troubleshooting

**Consent popups**: Click "Accept all" or "Reject all" in the snapshot.

**URL fast path didn't work**: Fall back to interactive. Some regions/locales handle `?q=` differently.

**No prices on calendar dates**: Prices lazy-load on hover. Hover over dates in the target month and wait 1-2s before snapshotting. This is especially common for far-future months.

**No results**: Verify airports (check combobox labels), dates in the future, or wait longer.

**RT business class shows "Reload"**: Google Flights aggressively rate-limits concurrent business class round-trip searches, especially for long-duration trips (30+ day gap). One-way searches are much more reliable. **Prefer one-way business searches** and combine the results. Only attempt RT if one-way data is insufficient.

**`get text body` returns garbage**: This is expected. On Google Flights, `get text body` returns raw JavaScript source code (160KB+), not rendered content. Always use `snapshot -i` instead.

**Bot detection / CAPTCHA on Google Flights**: Inform user. Do NOT solve CAPTCHAs.

**Airline website shows "maintenance" or error but site is actually up**: This is bot detection (Akamai/Cloudflare), not real maintenance. Use the Anti-Bot Fallback Pattern with stealth flags.

**`--profile`, `--args`, `--user-agent` ignored**: Daemon is already running with old args. Close ALL sessions first (`agent-browser session list` then close each), then relaunch.

## Deep-Dive Reference

See [references/interaction-patterns.md](references/interaction-patterns.md) for:
- Full annotated walkthrough (every command + expected output)
- Airport autocomplete failure modes and recovery
- Date picker calendar navigation
- Multi-city searches
- Scrolling for more results
