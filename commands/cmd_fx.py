"""
commands/cmd_fx.py
------------------
Implements the  FX [G10|EM]  command — Major FX Pairs Dashboard.

  fetch(parts)                  -> cache dict
  render(stdscr, cache, colors) -> None
  on_keypress(key, cache)       -> cache dict   ← arrow key navigation

Arrow key navigation (when FX is the active command):
  ← / →   toggle between G10 and EM screens  (triggers re-fetch)

Data priority:
  1. Finnhub  /forex/rates?base=USD   (1 call — all current spot rates)
             + /forex/candles per pair (1 call each — for day chg & 52W range)
  2. yfinance  EURUSD=X etc.          (fallback — 1 call per pair)

Finnhub /forex/rates response:
  {"base": "USD", "quote": {"EUR": 0.9213, "JPY": 149.55, ...}}
  Note: all values are "units of quote currency per 1 USD".
  For pairs like EUR/USD where EUR is the base, we invert: 1 / quote["EUR"].

Finnhub forex candle symbols use OANDA format: "OANDA:EUR_USD"
"""

import curses
import datetime
import time

import market_data

# ---------------------------------------------------------------------------
# Pair definitions
# (display_pair, fh_rates_key, fh_invert, fh_candle_symbol, yf_ticker)
#
# fh_rates_key : key in Finnhub /forex/rates quote dict
# fh_invert    : True  → displayed rate = 1 / quote[key]  (e.g. EUR/USD)
#                False → displayed rate = quote[key]       (e.g. USD/JPY)
# ---------------------------------------------------------------------------

G10_PAIRS = [
    ("EUR/USD", "EUR", True,  "OANDA:EUR_USD", "EURUSD=X"),
    ("GBP/USD", "GBP", True,  "OANDA:GBP_USD", "GBPUSD=X"),
    ("AUD/USD", "AUD", True,  "OANDA:AUD_USD", "AUDUSD=X"),
    ("NZD/USD", "NZD", True,  "OANDA:NZD_USD", "NZDUSD=X"),
    ("USD/JPY", "JPY", False, "OANDA:USD_JPY", "JPY=X"),
    ("USD/CAD", "CAD", False, "OANDA:USD_CAD", "CAD=X"),
    ("USD/CHF", "CHF", False, "OANDA:USD_CHF", "CHF=X"),
    ("USD/SEK", "SEK", False, "OANDA:USD_SEK", "SEK=X"),
    ("USD/NOK", "NOK", False, "OANDA:USD_NOK", "NOK=X"),
]

EM_PAIRS = [
    ("USD/CNH", "CNH", False, "OANDA:USD_CNH", "CNH=X"),
    ("USD/MXN", "MXN", False, "OANDA:USD_MXN", "MXN=X"),
    ("USD/BRL", "BRL", False, "OANDA:USD_BRL", "BRL=X"),
    ("USD/INR", "INR", False, "OANDA:USD_INR", "INR=X"),
    ("USD/KRW", "KRW", False, "OANDA:USD_KRW", "KRW=X"),
    ("USD/SGD", "SGD", False, "OANDA:USD_SGD", "SGD=X"),
    ("USD/HKD", "HKD", False, "OANDA:USD_HKD", "HKD=X"),
    ("USD/ZAR", "ZAR", False, "OANDA:USD_ZAR", "ZAR=X"),
]

VALID_SCREENS  = {"G10", "EM"}
SCREEN_CYCLE   = ["G10", "EM"]   # order for ← / → toggle
DEFAULT_SCREEN = "G10"


# ---------------------------------------------------------------------------
# Finnhub helpers
# ---------------------------------------------------------------------------

def _fetch_finnhub_rates():
    """Single call via server proxy — returns all spot rates vs USD."""
    raw = market_data.server_get("/api/forex/rates")
    return raw.get("quote", {})


def _fetch_finnhub_candles(symbol, from_ts, to_ts):
    """Daily candles via server proxy for one forex symbol over a date range."""
    raw = market_data.server_get("/api/forex/candles",
                                  params={"symbol": symbol, "resolution": "D",
                                          "from_ts": from_ts, "to_ts": to_ts})
    if raw.get("s") != "ok":
        return None
    return raw


def _fetch_finnhub_fx(pairs) -> list[dict]:
    """
    Fetch spot + 52W stats for all pairs via Finnhub.
    Returns list of row dicts (one per pair), None data on failure.
    """
    # Step 1: bulk spot rates — 1 call
    try:
        quote_map = _fetch_finnhub_rates()
    except Exception:
        quote_map = {}

    # Step 2: 1Y candles per pair for day-change + 52W high/low
    now_ts   = int(time.time())
    yr_ts    = now_ts - 365 * 86400

    rows = []
    for (display, fh_key, invert, candle_sym, _yf) in pairs:
        # Spot rate
        raw_rate = quote_map.get(fh_key)
        if raw_rate is None:
            rows.append({"pair": display, "data": None})
            continue

        rate = (1.0 / float(raw_rate)) if invert else float(raw_rate)

        # Candles for history
        try:
            candles = _fetch_finnhub_candles(candle_sym, yr_ts, now_ts)
        except Exception:
            candles = None

        if candles and len(candles.get("c", [])) >= 2:
            closes     = candles["c"]
            highs      = candles["h"]
            lows       = candles["l"]
            prev_close = closes[-2]

            # If inverted, invert candle data too
            if invert:
                closes     = [1/v for v in closes if v]
                highs_inv  = [1/v for v in lows  if v]   # low becomes high when inverted
                lows_inv   = [1/v for v in highs  if v]
                highs, lows = highs_inv, lows_inv
                prev_close = closes[-2] if len(closes) >= 2 else rate

            w52_high = max(highs)
            w52_low  = min(lows)
            w52_rng  = w52_high - w52_low
            position = ((rate - w52_low) / w52_rng * 100) if w52_rng else 50.0
            change     = rate - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0
        else:
            w52_high = w52_low = position = None
            change = change_pct = 0.0

        rows.append({"pair": display, "data": {
            "rate": rate, "change": change, "change_pct": change_pct,
            "w52_high": w52_high, "w52_low": w52_low, "w52_pos": position,
        }})

    return rows


# ---------------------------------------------------------------------------
# yfinance fallback
# ---------------------------------------------------------------------------

def _fetch_yfinance_fx(pairs) -> list[dict]:
    try:
        import yfinance as yf
    except ImportError:
        return [{"pair": p[0], "data": None} for p in pairs]

    rows = []
    for (display, _fh_key, _invert, _candle, yf_ticker) in pairs:
        try:
            hist = yf.Ticker(yf_ticker).history(period="1y", interval="1d")
            if hist.empty or len(hist) < 2:
                rows.append({"pair": display, "data": None})
                continue
            current    = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2])
            w52_high   = float(hist["High"].max())
            w52_low    = float(hist["Low"].min())
            w52_rng    = w52_high - w52_low
            change     = current - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0
            position   = ((current - w52_low) / w52_rng * 100) if w52_rng else 50.0
            rows.append({"pair": display, "data": {
                "rate": current, "change": change, "change_pct": change_pct,
                "w52_high": w52_high, "w52_low": w52_low, "w52_pos": position,
            }})
        except Exception:
            rows.append({"pair": display, "data": None})

    return rows


# ---------------------------------------------------------------------------
# fetch — called once on Enter (and again by on_keypress on screen switch)
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    screen = DEFAULT_SCREEN
    if len(parts) > 1 and parts[1].upper() in VALID_SCREENS:
        screen = parts[1].upper()

    pairs = G10_PAIRS if screen == "G10" else EM_PAIRS

    try:
        rows   = _fetch_finnhub_fx(pairs)
        source = "finnhub"
        # If all rows failed, fall through to yfinance
        if all(r["data"] is None for r in rows):
            raise ValueError("All Finnhub FX rows empty")
    except Exception as fh_err:
        try:
            rows   = _fetch_yfinance_fx(pairs)
            source = "yfinance"
        except Exception as yf_err:
            return {"error": f"finnhub: {fh_err}  |  yfinance: {yf_err}",
                    "screen": screen, "rows": [], "as_of": "", "source": ""}

    return {"error": None, "screen": screen, "rows": rows,
            "as_of": datetime.date.today().isoformat(), "source": source}


# ---------------------------------------------------------------------------
# on_keypress — toggle G10 / EM with ← / →
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    """
    ← / →  toggle between G10 and EM screens (triggers re-fetch).
    """
    if key not in (curses.KEY_LEFT, curses.KEY_RIGHT):
        return cache

    current = cache.get("screen", DEFAULT_SCREEN)
    idx     = SCREEN_CYCLE.index(current) if current in SCREEN_CYCLE else 0

    if key == curses.KEY_RIGHT:
        new_screen = SCREEN_CYCLE[(idx + 1) % len(SCREEN_CYCLE)]
    else:
        new_screen = SCREEN_CYCLE[(idx - 1) % len(SCREEN_CYCLE)]

    # Re-fetch for the new screen
    return fetch(["FX", new_screen])


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr); stdscr.addstr(row, col, text); stdscr.attroff(attr)
    except Exception:
        pass


def _fmt_rate(pair, val):
    if val is None: return "    N/A"
    if any(x in pair for x in ("JPY", "KRW")): return f"{val:>9.2f}"
    return f"{val:>9.4f}"


def _fmt_chg(pair, val):
    if val is None: return "    N/A"
    if any(x in pair for x in ("JPY", "KRW")): return f"{val:>+8.2f}"
    return f"{val:>+8.4f}"


def _fmt_pct(val):
    if val is None: return "    N/A"
    return f"{val:>+7.2f}%"


def _range_bar(position, width=12):
    if position is None: return " " * (width + 2)
    filled = max(0, min(width, int(position / 100 * width)))
    return f"[{'=' * filled + '·' * (width - filled)}]"


# ---------------------------------------------------------------------------
# render — called every frame
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    _, width = stdscr.getmaxyx()

    if cache.get("error"):
        _put(stdscr, 4, 0, f"  Error: {cache['error']}", colors["negative"])
        return

    screen = cache.get("screen", DEFAULT_SCREEN)
    rows   = cache.get("rows", [])
    as_of  = cache.get("as_of", "")
    source = cache.get("source", "")

    if not rows:
        _put(stdscr, 4, 0, "  Loading FX data...", colors["dim"]); return

    sep = f"  {'─' * 65}"
    r   = 4

    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    title = f"FX MAJORS  —  {'G10 Pairs' if screen == 'G10' else 'Emerging Markets'}"
    _put(stdscr, r, 2, title, colors["orange"], bold=True)
    _put(stdscr, r, len(title) + 4, f"As of {as_of}  [{source}]", colors["dim"])
    r += 1

    # Tab bar
    col = 2
    for tab in SCREEN_CYCLE:
        if tab == screen:
            _put(stdscr, r, col, f"[ {tab} ]", colors["orange"], bold=True)
            col += 8
        else:
            _put(stdscr, r, col, f"  {tab}  ", colors["dim"])
            col += 6
    _put(stdscr, r, col + 2, "← → switch   |   FX G10  /  FX EM", colors["dim"])
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    COL_PAIR  = 2
    COL_RATE  = 12
    COL_CHG   = 23
    COL_PCT   = 33
    COL_HIGH  = 43
    COL_LOW   = 53
    COL_BAR   = 63

    _put(stdscr, r, COL_PAIR, f"{'Pair':<9}",    colors["dim"], bold=True)
    _put(stdscr, r, COL_RATE, f"{'Rate':>9}",     colors["dim"], bold=True)
    _put(stdscr, r, COL_CHG,  f"{'Day Chg':>9}",  colors["dim"], bold=True)
    _put(stdscr, r, COL_PCT,  f"{'Day %':>8}",     colors["dim"], bold=True)
    _put(stdscr, r, COL_HIGH, f"{'52W Hi':>8}",    colors["dim"], bold=True)
    _put(stdscr, r, COL_LOW,  f"{'52W Lo':>8}",    colors["dim"], bold=True)
    if COL_BAR < width - 16:
        _put(stdscr, r, COL_BAR, "52W Range", colors["dim"], bold=True)
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    for item in rows:
        pair = item["pair"]
        data = item["data"]

        if data is None:
            _put(stdscr, r, COL_PAIR, f"{pair:<9}", colors["dim"])
            _put(stdscr, r, COL_RATE, "       N/A", colors["dim"])
            r += 1
            continue

        cc = colors["positive"] if data["change"] >= 0 else colors["negative"]
        yc = (colors["positive"] if (data.get("w52_pos") or 50) > 50
              else colors["negative"])

        _put(stdscr, r, COL_PAIR, f"{pair:<9}",                    colors["orange"], bold=True)
        _put(stdscr, r, COL_RATE, _fmt_rate(pair, data["rate"]),    colors["orange"], bold=True)
        _put(stdscr, r, COL_CHG,  _fmt_chg(pair, data["change"]),   cc)
        _put(stdscr, r, COL_PCT,  _fmt_pct(data["change_pct"]),     cc)
        _put(stdscr, r, COL_HIGH, _fmt_rate(pair, data["w52_high"]), colors["dim"])
        _put(stdscr, r, COL_LOW,  _fmt_rate(pair, data["w52_low"]),  colors["dim"])
        if COL_BAR < width - 16:
            _put(stdscr, r, COL_BAR, _range_bar(data["w52_pos"]), yc)
        r += 1

    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2,
         "Spot rates. Finnhub primary / yfinance fallback. 15-min delay may apply.",
         colors["dim"])