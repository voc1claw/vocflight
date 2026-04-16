"""Hardcoded rules and configuration. No external files needed."""

import os
import tempfile

# Airlines that must NEVER appear in results
BANNED_AIRLINES = {
    "air india",
    "ethiopian",
    "kenya airways",
    "royal jordanian",
    "thai airways",
    "thai",  # sometimes listed as just "THAI"
}

# Minimum cabin class — always search this
CABIN_MINIMUM = "business"

# Max concurrent browser sessions (VM resource limit)
MAX_SESSIONS = 4

# Default number of dates to search when user gives flexible window
DEFAULT_RANGE_DAYS = 7
MAX_RANGE_DAYS = 14

# Signal message character limit
SIGNAL_MSG_LIMIT = 2000

# Flights per Signal message chunk
FLIGHTS_PER_MESSAGE = 4

# Max flights shown per direction in date-pair output (keeps both directions visible at a glance)
MAX_FLIGHTS_PER_DIRECTION = 3

# agent-browser binary — direct path to native executable (bypasses shell shims)
AGENT_BROWSER = r"C:\Users\admin\AppData\Roaming\npm\node_modules\agent-browser\bin\agent-browser-win32-x64.exe"

# Swoop retry settings
SWOOP_MAX_RETRIES = 2
SWOOP_RETRY_DELAY = 0.5  # seconds between retries (doubles each attempt)
SWOOP_INTER_SEARCH_DELAY = 0.1  # seconds between searches (minimal, swoop handles its own rate limits)

# Parallel swoop search settings
SWOOP_MAX_WORKERS = 12  # max concurrent swoop RPC threads (tuned for Render free tier 512MB)

# Hard ceiling on one search phase before returning partial results
SWOOP_PHASE_TIMEOUT_SECONDS = 50

# Disk cache settings — use tempdir so it works on Linux (Render) and Windows
CACHE_DIR = os.path.join(tempfile.gettempdir(), "vocflight_cache")
CACHE_TTL_SECONDS = 1800  # 30 minutes

# ---------------------------------------------------------------------------
# Singapore Airlines browser scraping settings
# ---------------------------------------------------------------------------
# No API key needed — scrapes singaporeair.com directly via agent-browser
# with Akamai bypass (headed + AutomationControlled disabled)
