"""
commands/cmd_omon.py
--------------------
Implements the  OMON <TICKER>  command — Options Chain Monitor.

  OMON AAPL     <- full options chain for AAPL

Navigation (PANE mode):
  ← / →   cycle expiration dates (fetches each chain lazily on first visit)
  ↑ / ↓   scroll through strike rows

Cache structure:
  {
    "ticker":  str,
    "dates":   [str, ...],       # all expiry dates — fetched once on Enter
    "chains":  {date: {...}},    # lazily populated per expiry as user navigates
    "exp_idx": int,
    "scroll":  int,
    "error":   str | None,
  }
"""

import curses
import market_data

_VISIBLE_ROWS = 18


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    if col < 0:
        return
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr)
        stdscr.addstr(row, col, text)
        stdscr.attroff(attr)
    except Exception:
        pass


def _fmt_price(v) -> str:
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "--"


def _fmt_iv(v) -> str:
    try:
        f = float(v)
        pct = f * 100 if f < 5 else f
        return f"{pct:.1f}%"
    except Exception:
        return "--"


def _fmt_vol(v) -> str:
    try:
        val = int(v)
        if val >= 1_000_000:
            return f"{val / 1_000_000:.1f}M"
        if val >= 1_000:
            return f"{val // 1_000}K"
        return str(val)
    except Exception:
        return "--"


def _fetch_chain(ticker: str, expiry: str) -> dict | None:
    """Fetch a single expiry chain from the server. Returns None on failure."""
    try:
        return market_data.server_get(f"/api/options/{ticker}/chain/{expiry}")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# fetch — called once on Enter; gets dates + first chain only
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    ticker = parts[1].upper() if len(parts) > 1 else None
    if not ticker:
        return {
            "error": "Usage: OMON <TICKER>   e.g. OMON AAPL",
            "ticker": None, "dates": [], "chains": {}, "exp_idx": 0, "scroll": 0,
        }

    try:
        raw   = market_data.server_get(f"/api/options/{ticker}/dates")
        dates = raw.get("dates", []) if isinstance(raw, dict) else []

        if not dates:
            return {
                "error": f"No options data found for {ticker}.",
                "ticker": ticker, "dates": [], "chains": {}, "exp_idx": 0, "scroll": 0,
            }

        # Pre-fetch the first expiry so the screen is populated immediately
        chains = {}
        first  = _fetch_chain(ticker, dates[0])
        if first:
            chains[dates[0]] = first

        return {
            "error":   None,
            "ticker":  ticker,
            "dates":   dates,
            "chains":  chains,
            "exp_idx": 0,
            "scroll":  0,
        }

    except Exception as e:
        return {
            "error": str(e), "ticker": ticker,
            "dates": [], "chains": {}, "exp_idx": 0, "scroll": 0,
        }


# ---------------------------------------------------------------------------
# on_keypress — ← → change expiry (lazy fetch), ↑ ↓ scroll strikes
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    dates   = cache.get("dates", [])
    chains  = cache.get("chains", {})
    ticker  = cache.get("ticker", "")
    n       = len(dates)
    exp_idx = cache.get("exp_idx", 0)
    scroll  = cache.get("scroll", 0)

    if key == curses.KEY_LEFT:
        new_idx = max(0, exp_idx - 1)
        expiry  = dates[new_idx] if dates else None
        if expiry and expiry not in chains:
            chain = _fetch_chain(ticker, expiry)
            if chain:
                chains = {**chains, expiry: chain}
        return {**cache, "chains": chains, "exp_idx": new_idx, "scroll": 0}

    if key == curses.KEY_RIGHT:
        new_idx = min(n - 1, exp_idx + 1) if n else 0
        expiry  = dates[new_idx] if dates else None
        if expiry and expiry not in chains:
            chain = _fetch_chain(ticker, expiry)
            if chain:
                chains = {**chains, expiry: chain}
        return {**cache, "chains": chains, "exp_idx": new_idx, "scroll": 0}

    if key == curses.KEY_UP:
        return {**cache, "scroll": max(0, scroll - 1)}

    if key == curses.KEY_DOWN:
        return {**cache, "scroll": scroll + 1}

    return cache


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    _, width = stdscr.getmaxyx()
    sep = f"  {'─' * (width - 3)}"
    r   = 4

    error = cache.get("error")
    if error:
        _put(stdscr, r, 0, f"  Error: {error}", colors["negative"])
        return

    ticker  = cache.get("ticker", "")
    dates   = cache.get("dates", [])
    chains  = cache.get("chains", {})
    exp_idx = cache.get("exp_idx", 0)
    scroll  = cache.get("scroll", 0)

    if not dates:
        _put(stdscr, r, 2, "  Loading...", colors["dim"])
        return

    exp_idx = min(exp_idx, len(dates) - 1)
    date    = dates[exp_idx]
    chain   = chains.get(date)

    # ── Header ────────────────────────────────────────────────────────────
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, f"OMON  {ticker}", colors["orange"], bold=True)
    _put(stdscr, r, 10 + len(ticker), f"  Options Chain  —  {date}", colors["dim"])
    r += 1

    nav = []
    if exp_idx > 0:
        nav.append(f"←  {dates[exp_idx - 1]}")
    nav.append(f"[ {exp_idx + 1} of {len(dates)} ]")
    if exp_idx < len(dates) - 1:
        nav.append(f"{dates[exp_idx + 1]}  →")
    _put(stdscr, r, 2, "   ".join(nav), colors["dim"])
    hint = "← → expiry   ↑ ↓ scroll"
    _put(stdscr, r, max(2, width - len(hint) - 2), hint, colors["dim"])
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    if chain is None:
        _put(stdscr, r, 2, "  Fetching chain...", colors["dim"])
        return

    calls = chain.get("calls", [])
    puts  = chain.get("puts",  [])

    # ── Build strike index ────────────────────────────────────────────────
    call_map = {c.get("strike", 0): c for c in calls}
    put_map  = {p.get("strike", 0): p for p in puts}
    strikes  = sorted(set(list(call_map.keys()) + list(put_map.keys())))

    if not strikes:
        _put(stdscr, r, 2, "No contracts found for this expiry.", colors["dim"])
        return

    max_scroll = max(0, len(strikes) - _VISIBLE_ROWS)
    scroll     = min(scroll, max_scroll)

    # ── Column layout — symmetric around the strike column ───────────────
    #
    #   CALLS (green/dim)            STRIKE    PUTS (red/dim)
    #   IV    Bid   Ask   Vol   OI | STRIKE | OI   Vol   Ask   Bid   IV
    #
    strike_col = width // 2 - 4

    C_OI_L  = strike_col - 7
    C_VOL_L = C_OI_L  - 7
    C_ASK_L = C_VOL_L - 8
    C_BID_L = C_ASK_L - 8
    C_IV_L  = C_BID_L - 7

    C_STR   = strike_col
    C_OI_R  = strike_col + 10
    C_VOL_R = C_OI_R  + 7
    C_ASK_R = C_VOL_R + 7
    C_BID_R = C_ASK_R + 8
    C_IV_R  = C_BID_R + 8

    # Side labels
    if C_IV_L > 2:
        _put(stdscr, r, C_IV_L, "─── CALLS ───", colors["positive"], bold=True)
    _put(stdscr, r, C_OI_R, "─── PUTS ───", colors["negative"], bold=True)
    r += 1

    # Column headers
    if C_IV_L > 2:
        _put(stdscr, r, C_IV_L,  f"{'IV':>6}",    colors["dim"], bold=True)
    _put(stdscr, r, C_BID_L, f"{'Bid':>7}",    colors["dim"], bold=True)
    _put(stdscr, r, C_ASK_L, f"{'Ask':>7}",    colors["dim"], bold=True)
    _put(stdscr, r, C_VOL_L, f"{'Vol':>7}",    colors["dim"], bold=True)
    _put(stdscr, r, C_OI_L,  f"{'OI':>6}",     colors["dim"], bold=True)
    _put(stdscr, r, C_STR,   f"{'STRIKE':^9}", colors["orange"], bold=True)
    _put(stdscr, r, C_OI_R,  f"{'OI':<6}",     colors["dim"], bold=True)
    _put(stdscr, r, C_VOL_R, f"{'Vol':<7}",    colors["dim"], bold=True)
    _put(stdscr, r, C_ASK_R, f"{'Ask':<7}",    colors["dim"], bold=True)
    _put(stdscr, r, C_BID_R, f"{'Bid':<7}",    colors["dim"], bold=True)
    if C_IV_R < width - 6:
        _put(stdscr, r, C_IV_R, f"{'IV':<6}",  colors["dim"], bold=True)
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    # ── Strike rows ───────────────────────────────────────────────────────
    for strike in strikes[scroll: scroll + _VISIBLE_ROWS]:
        c = call_map.get(strike, {})
        p = put_map.get(strike, {})

        call_color = colors["positive"] if c.get("inTheMoney") else colors["dim"]
        put_color  = colors["negative"] if p.get("inTheMoney") else colors["dim"]

        if C_IV_L > 2:
            _put(stdscr, r, C_IV_L,  f"{_fmt_iv(c.get('impliedVolatility')):>6}",  call_color)
        _put(stdscr, r, C_BID_L, f"{_fmt_price(c.get('bid')):>7}",   call_color)
        _put(stdscr, r, C_ASK_L, f"{_fmt_price(c.get('ask')):>7}",   call_color)
        _put(stdscr, r, C_VOL_L, f"{_fmt_vol(c.get('volume')):>7}",  call_color)
        _put(stdscr, r, C_OI_L,  f"{_fmt_vol(c.get('openInterest')):>6}", call_color)
        _put(stdscr, r, C_STR,   f"{strike:^9.2f}", colors["orange"], bold=True)
        _put(stdscr, r, C_OI_R,  f"{_fmt_vol(p.get('openInterest')):<6}", put_color)
        _put(stdscr, r, C_VOL_R, f"{_fmt_vol(p.get('volume')):<7}",  put_color)
        _put(stdscr, r, C_ASK_R, f"{_fmt_price(p.get('ask')):<7}",   put_color)
        _put(stdscr, r, C_BID_R, f"{_fmt_price(p.get('bid')):<7}",   put_color)
        if C_IV_R < width - 6:
            _put(stdscr, r, C_IV_R, f"{_fmt_iv(p.get('impliedVolatility')):<6}", put_color)
        r += 1

    # Scroll indicator
    total = len(strikes)
    if total > _VISIBLE_ROWS:
        shown_end = min(scroll + _VISIBLE_ROWS, total)
        _put(stdscr, r, 0, sep, colors["dim"]); r += 1
        _put(stdscr, r, 2,
             f"Strikes {scroll + 1}–{shown_end} of {total}   ↑ ↓ to scroll",
             colors["dim"])
    else:
        _put(stdscr, r, 0, sep, colors["dim"])
