"""
commands/cmd_omon.py
--------------------
Implements the  OMON <TICKER>  command — Options Chain Monitor.

  OMON AAPL     <- full options chain for AAPL

Navigation (PANE mode):
  ← / →   cycle expiration dates
  ↑ / ↓   scroll through strike rows
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
        # Finnhub returns IV as a decimal (0.25 = 25%) — scale if < 5
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


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    ticker = parts[1].upper() if len(parts) > 1 else None
    if not ticker:
        return {
            "error": "Usage: OMON <TICKER>   e.g. OMON AAPL",
            "ticker": None, "expiries": [], "exp_idx": 0, "scroll": 0,
        }

    try:
        raw = market_data.server_get(f"/api/options/{ticker}")

        if not isinstance(raw, dict):
            return {
                "error": f"Unexpected response for {ticker}",
                "ticker": ticker, "expiries": [], "exp_idx": 0, "scroll": 0,
            }

        data_list = raw.get("data", [])
        if not data_list:
            return {
                "error": f"No options data found for {ticker}. Finnhub may not cover this symbol.",
                "ticker": ticker, "expiries": [], "exp_idx": 0, "scroll": 0,
            }

        expiries = []
        for entry in data_list:
            exp_date = entry.get("expirationDate", "")
            options  = entry.get("options", {})
            calls    = options.get("CALL", [])
            puts     = options.get("PUT", [])
            if exp_date:
                expiries.append({"date": exp_date, "calls": calls, "puts": puts})

        expiries.sort(key=lambda x: x["date"])

        return {
            "error":    None,
            "ticker":   ticker,
            "expiries": expiries,
            "exp_idx":  0,
            "scroll":   0,
        }

    except Exception as e:
        return {
            "error": str(e), "ticker": ticker,
            "expiries": [], "exp_idx": 0, "scroll": 0,
        }


# ---------------------------------------------------------------------------
# on_keypress — ← → change expiry, ↑ ↓ scroll strikes
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    expiries = cache.get("expiries", [])
    n        = len(expiries)
    exp_idx  = cache.get("exp_idx", 0)
    scroll   = cache.get("scroll", 0)

    if key == curses.KEY_LEFT:
        return {**cache, "exp_idx": max(0, exp_idx - 1), "scroll": 0}

    if key == curses.KEY_RIGHT:
        return {**cache, "exp_idx": min(n - 1, exp_idx + 1) if n else 0, "scroll": 0}

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

    ticker   = cache.get("ticker", "")
    expiries = cache.get("expiries", [])
    exp_idx  = cache.get("exp_idx", 0)
    scroll   = cache.get("scroll", 0)

    if not expiries:
        _put(stdscr, r, 2, "  Loading...", colors["dim"])
        return

    exp_idx = min(exp_idx, len(expiries) - 1)
    exp     = expiries[exp_idx]
    date    = exp["date"]
    calls   = exp.get("calls", [])
    puts    = exp.get("puts", [])

    # ── Header ────────────────────────────────────────────────────────────
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, f"OMON  {ticker}", colors["orange"], bold=True)
    _put(stdscr, r, 10 + len(ticker), f"  Options Chain  —  {date}", colors["dim"])
    r += 1

    # Expiry navigation line
    nav = []
    if exp_idx > 0:
        nav.append(f"←  {expiries[exp_idx - 1]['date']}")
    nav.append(f"[ {exp_idx + 1} of {len(expiries)} ]")
    if exp_idx < len(expiries) - 1:
        nav.append(f"{expiries[exp_idx + 1]['date']}  →")
    _put(stdscr, r, 2, "   ".join(nav), colors["dim"])
    hint = "← → expiry   ↑ ↓ scroll"
    _put(stdscr, r, max(2, width - len(hint) - 2), hint, colors["dim"])
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    # ── Build strike index ────────────────────────────────────────────────
    call_map = {c.get("strike", 0): c for c in calls}
    put_map  = {p.get("strike", 0): p for p in puts}
    strikes  = sorted(set(list(call_map.keys()) + list(put_map.keys())))

    if not strikes:
        _put(stdscr, r, 2, "No contracts found for this expiry.", colors["dim"])
        return

    # Clamp scroll
    max_scroll = max(0, len(strikes) - _VISIBLE_ROWS)
    scroll = min(scroll, max_scroll)

    # ── Column layout — symmetric around the strike column ───────────────
    #
    #   CALLS (green/dim)            STRIKE    PUTS (red/dim)
    #   IV    Bid   Ask   Vol   OI | STRIKE | OI   Vol   Ask   Bid   IV
    #
    strike_col = width // 2 - 4   # centre of 9-char strike field

    # CALLS columns — right-aligned approaching strike
    C_OI_L  = strike_col - 7
    C_VOL_L = C_OI_L  - 7
    C_ASK_L = C_VOL_L - 8
    C_BID_L = C_ASK_L - 8
    C_IV_L  = C_BID_L - 7

    # PUTS columns — left-aligned from strike
    C_STR   = strike_col
    C_OI_R  = strike_col + 10
    C_VOL_R = C_OI_R  + 7
    C_ASK_R = C_VOL_R + 7
    C_BID_R = C_ASK_R + 8
    C_IV_R  = C_BID_R + 8

    # Side labels row
    if C_IV_L > 2:
        _put(stdscr, r, C_IV_L, "─── CALLS ───", colors["positive"], bold=True)
    _put(stdscr, r, C_OI_R, "─── PUTS ───", colors["negative"], bold=True)
    r += 1

    # Column headers
    if C_IV_L > 2:
        _put(stdscr, r, C_IV_L,  f"{'IV':>6}",   colors["dim"], bold=True)
    _put(stdscr, r, C_BID_L, f"{'Bid':>7}",   colors["dim"], bold=True)
    _put(stdscr, r, C_ASK_L, f"{'Ask':>7}",   colors["dim"], bold=True)
    _put(stdscr, r, C_VOL_L, f"{'Vol':>7}",   colors["dim"], bold=True)
    _put(stdscr, r, C_OI_L,  f"{'OI':>6}",    colors["dim"], bold=True)
    _put(stdscr, r, C_STR,   f"{'STRIKE':^9}", colors["orange"], bold=True)
    _put(stdscr, r, C_OI_R,  f"{'OI':<6}",    colors["dim"], bold=True)
    _put(stdscr, r, C_VOL_R, f"{'Vol':<7}",   colors["dim"], bold=True)
    _put(stdscr, r, C_ASK_R, f"{'Ask':<7}",   colors["dim"], bold=True)
    _put(stdscr, r, C_BID_R, f"{'Bid':<7}",   colors["dim"], bold=True)
    if C_IV_R < width - 6:
        _put(stdscr, r, C_IV_R, f"{'IV':<6}",  colors["dim"], bold=True)
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    # ── Strike rows ───────────────────────────────────────────────────────
    visible = strikes[scroll: scroll + _VISIBLE_ROWS]

    for strike in visible:
        c = call_map.get(strike, {})
        p = put_map.get(strike, {})

        c_itm      = c.get("inTheMoney", False)
        p_itm      = p.get("inTheMoney", False)
        call_color = colors["positive"] if c_itm else colors["dim"]
        put_color  = colors["negative"] if p_itm else colors["dim"]

        c_iv  = _fmt_iv(c.get("impliedVolatility"))
        c_bid = _fmt_price(c.get("bid"))
        c_ask = _fmt_price(c.get("ask"))
        c_vol = _fmt_vol(c.get("volume"))
        c_oi  = _fmt_vol(c.get("openInterest"))

        p_iv  = _fmt_iv(p.get("impliedVolatility"))
        p_bid = _fmt_price(p.get("bid"))
        p_ask = _fmt_price(p.get("ask"))
        p_vol = _fmt_vol(p.get("volume"))
        p_oi  = _fmt_vol(p.get("openInterest"))

        if C_IV_L > 2:
            _put(stdscr, r, C_IV_L,  f"{c_iv:>6}",   call_color)
        _put(stdscr, r, C_BID_L, f"{c_bid:>7}",   call_color)
        _put(stdscr, r, C_ASK_L, f"{c_ask:>7}",   call_color)
        _put(stdscr, r, C_VOL_L, f"{c_vol:>7}",   call_color)
        _put(stdscr, r, C_OI_L,  f"{c_oi:>6}",    call_color)
        _put(stdscr, r, C_STR,   f"{strike:^9.2f}", colors["orange"], bold=True)
        _put(stdscr, r, C_OI_R,  f"{p_oi:<6}",    put_color)
        _put(stdscr, r, C_VOL_R, f"{p_vol:<7}",   put_color)
        _put(stdscr, r, C_ASK_R, f"{p_ask:<7}",   put_color)
        _put(stdscr, r, C_BID_R, f"{p_bid:<7}",   put_color)
        if C_IV_R < width - 6:
            _put(stdscr, r, C_IV_R, f"{p_iv:<6}",  put_color)

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
