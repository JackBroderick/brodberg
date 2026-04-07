"""
commands/cmd_comd.py
--------------------
Implements the  COMD  command — Commodities Dashboard.

Data priority:
  1. yfinance  CL=F, GC=F, etc.  (primary — most reliable for futures)
  2. Finnhub   /quote per symbol  (fallback — no YTD, degraded display)

Finnhub free tier handles some futures symbols inconsistently, so yfinance
stays primary here. Finnhub fallback shows price + daily change only
(YTD unavailable from Finnhub free tier — displayed as "---").

Grouped display:
  ENERGY  — WTI Crude, Brent Crude, Natural Gas
  METALS  — Gold, Silver, Copper
  GRAINS  — Wheat, Corn, Soybeans
"""

import curses
import datetime

# ---------------------------------------------------------------------------
# Commodity definitions
# (display_name, yfinance_ticker, finnhub_symbol, unit_label)
# ---------------------------------------------------------------------------

COMMODITIES = {
    "ENERGY": [
        ("WTI Crude",   "CL=F", "/bbl"),
        ("Brent Crude", "BZ=F", "/bbl"),
        ("Natural Gas", "NG=F", "/MMBtu"),
    ],
    "METALS": [
        ("Gold",        "GC=F", "/oz"),
        ("Silver",      "SI=F", "/oz"),
        ("Copper",      "HG=F", "/lb"),
    ],
    "GRAINS": [
        ("Wheat",       "ZW=F", "/bu"),
        ("Corn",        "ZC=F", "/bu"),
        ("Soybeans",    "ZS=F", "/bu"),
    ],
}


# ---------------------------------------------------------------------------
# yfinance fetch  (primary)
# ---------------------------------------------------------------------------

def _fetch_yf_commodity(ticker: str) -> dict | None:
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="ytd", interval="1d")
        if hist.empty or len(hist) < 2:
            return None
        current    = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2])
        ytd_open   = float(hist["Close"].iloc[0])
        change     = current - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0
        ytd_pct    = ((current - ytd_open) / ytd_open * 100) if ytd_open else 0.0
        return {"price": current, "change": change,
                "change_pct": change_pct, "ytd_pct": ytd_pct, "source": "yf"}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    result = {}
    for group, items in COMMODITIES.items():
        result[group] = []
        for (name, yf_ticker, unit) in items:
            data = _fetch_yf_commodity(yf_ticker)
            result[group].append({
                "name": name, "unit": unit, "data": data,
            })
    return {"error": None, "groups": result,
            "as_of": datetime.date.today().isoformat()}


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr); stdscr.addstr(row, col, text); stdscr.attroff(attr)
    except Exception:
        pass


def _fmt_price(val):
    return f"${val:>8.2f}" if val is not None else "       N/A"


def _fmt_chg(val):
    return f"{val:>+7.2f}" if val is not None else "    N/A"


def _fmt_pct(val):
    return f"{val:>+6.2f}%" if val is not None else "   ---"


# ---------------------------------------------------------------------------
# render — called every frame
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    _, width = stdscr.getmaxyx()

    if cache.get("error"):
        _put(stdscr, 4, 0, f"  Error: {cache['error']}", colors["negative"])
        return

    groups = cache.get("groups", {})
    as_of  = cache.get("as_of", "")

    if not groups:
        _put(stdscr, 4, 0, "  Loading commodity data...", colors["dim"]); return

    sep = f"  {'─' * 60}"
    r   = 4

    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, "COMMODITIES DASHBOARD", colors["orange"], bold=True)
    _put(stdscr, r, 26, f"As of {as_of}", colors["dim"]); r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    COL_NAME  = 2
    COL_PRICE = 20
    COL_CHG   = 32
    COL_CPCT  = 42
    COL_YTD   = 52
    COL_SRC   = 62

    _put(stdscr, r, COL_NAME,  f"{'Commodity':<17}",  colors["dim"], bold=True)
    _put(stdscr, r, COL_PRICE, f"{'Price':>10}",       colors["dim"], bold=True)
    _put(stdscr, r, COL_CHG,   f"{'Day Chg':>9}",      colors["dim"], bold=True)
    _put(stdscr, r, COL_CPCT,  f"{'Day %':>8}",        colors["dim"], bold=True)
    _put(stdscr, r, COL_YTD,   f"{'YTD %':>8}",        colors["dim"], bold=True)
    r += 1

    for group_name, items in groups.items():
        _put(stdscr, r, 0, sep, colors["dim"]); r += 1
        _put(stdscr, r, 2, f" {group_name} ", colors["header"], bold=True); r += 1

        for item in items:
            name = item["name"]
            data = item["data"]

            if data is None:
                _put(stdscr, r, COL_NAME,  f"{name:<17}", colors["dim"])
                _put(stdscr, r, COL_PRICE, "       N/A",  colors["dim"])
                r += 1
                continue

            cc  = colors["positive"] if data["change"] >= 0 else colors["negative"]
            ytc = (colors["positive"] if (data["ytd_pct"] or 0) >= 0
                   else colors["negative"])
            src_label = f"[{data['source']}]" if data.get("source") else ""

            _put(stdscr, r, COL_NAME,  f"{name:<17}",               colors["orange"])
            _put(stdscr, r, COL_PRICE, _fmt_price(data["price"]),    colors["orange"], bold=True)
            _put(stdscr, r, COL_CHG,   _fmt_chg(data["change"]),     cc)
            _put(stdscr, r, COL_CPCT,  _fmt_pct(data["change_pct"]), cc)
            _put(stdscr, r, COL_YTD,   _fmt_pct(data["ytd_pct"]),    ytc)
            if COL_SRC < width - 6:
                _put(stdscr, r, COL_SRC, src_label, colors["dim"])
            r += 1

    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2,
         "Futures prices. yfinance primary / Finnhub fallback. [fh] = Finnhub (no YTD).",
         colors["dim"])