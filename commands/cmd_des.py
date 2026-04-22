"""
commands/cmd_des.py
-------------------
Implements the  DES <TICKER>  command (Description / Company Profile).

  fetch(parts)                  -> cache dict
  render(stdscr, cache, colors) -> None

Pulls Finnhub Company Profile 2 for the requested ticker and renders a
Bloomberg-style company overview panel.

Usage:
  DES AAPL
  DES TSLA
"""

import curses

import market_data


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def _fetch_profile(ticker: str) -> dict:
    """
    Fetch company profile via the Broderick Terminal server proxy.
    Returns the raw JSON dict or raises on failure.
    """
    return market_data.server_get(f"/api/company/{ticker.upper()}")


def _fmt_market_cap(raw) -> str:
    """
    Finnhub returns marketCapitalization in millions of USD.
    Format it as $X.XXB / $X.XXM for readability.
    """
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return "N/A"
    if val >= 1_000:
        return f"${val / 1_000:.2f}B"
    return f"${val:.2f}M"


def _fmt_shares(raw) -> str:
    """Format shareOutstanding (millions) with commas."""
    try:
        val = float(raw)
        return f"{val:,.2f}M shares"
    except (TypeError, ValueError):
        return "N/A"


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    """
    parts = ["DES", "AAPL"]  (already upper-cased by the registry)
    Returns a cache dict with keys "data" and "error".
    """
    ticker = parts[1] if len(parts) > 1 else None
    if not ticker:
        return {"data": None, "error": "Usage: DES <TICKER>   e.g. DES AAPL"}

    try:
        raw = _fetch_profile(ticker)
        if not raw or not raw.get("name"):
            return {"data": None, "error": f"No profile data found for '{ticker}'. Check the ticker symbol."}

        data = {
            "symbol":       raw.get("ticker",                  ticker.upper()),
            "name":         raw.get("name",                    "N/A"),
            "exchange":     raw.get("exchange",                "N/A"),
            "industry":     raw.get("finnhubIndustry",         "N/A"),
            "country":      raw.get("country",                 "N/A"),
            "currency":     raw.get("currency",                "N/A"),
            "ipo":          raw.get("ipo",                     "N/A"),
            "market_cap":   _fmt_market_cap(raw.get("marketCapitalization")),
            "shares_out":   _fmt_shares(raw.get("shareOutstanding")),
            "phone":        raw.get("phone",                   "N/A"),
            "website":      raw.get("weburl",                  "N/A"),
        }
        return {"data": data, "error": None}

    except Exception as e:
        return {"data": None, "error": str(e)}


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    """
    Render the company profile panel starting at row 4.
    Rows 0-3 are reserved chrome (header / benchmarks / news / breathing room).
    """
    error = cache.get("error")
    if error:
        try:
            stdscr.attron(colors["negative"])
            stdscr.addstr(4, 0, f"  Error: {error}")
            stdscr.attroff(colors["negative"])
        except Exception:
            pass
        return

    d = cache.get("data")
    if not d:
        try:
            stdscr.attron(colors["dim"])
            stdscr.addstr(4, 0, "  Loading...")
            stdscr.attroff(colors["dim"])
        except Exception:
            pass
        return

    sep       = f"  {'─' * 50}"
    sep_short = f"  {'─' * 24}"

    def put(row, col, text, color, bold=False):
        attr = color | (curses.A_BOLD if bold else 0)
        try:
            stdscr.attron(attr)
            stdscr.addstr(row, col, text)
            stdscr.attroff(attr)
        except Exception:
            pass

    def label_value(row, col, label, value, val_color=None):
        """Draw a left-aligned label in dim and its value in orange (or val_color)."""
        lbl = f"  {label:<20}"
        put(row, col, lbl, colors["dim"])
        put(row, col + len(lbl), value, val_color or colors["orange"])

    r = 4

    # ── Header block ─────────────────────────────────────────────────────
    put(r,     0, sep,                                      colors["dim"])
    put(r + 1, 0, f"   {d['symbol']}",                     colors["orange"], bold=True)
    put(r + 1, 8, f"  {d['name']}",                        colors["orange"], bold=True)
    put(r + 2, 0, f"   {d['exchange']}  |  {d['country']}  |  {d['currency']}",
        colors["dim"])
    put(r + 3, 0, sep,                                      colors["dim"])

    # ── Two-column data grid ─────────────────────────────────────────────
    # Left column starts at col 0, right column at col 40
    LEFT  = 0
    RIGHT = 44

    label_value(r + 4,  LEFT,  "Industry:",       d["industry"])
    label_value(r + 4,  RIGHT, "IPO Date:",        d["ipo"])

    label_value(r + 5,  LEFT,  "Market Cap:",     d["market_cap"])
    label_value(r + 5,  RIGHT, "Shares Out:",      d["shares_out"])

    label_value(r + 6,  LEFT,  "Exchange:",       d["exchange"])
    label_value(r + 6,  RIGHT, "Currency:",        d["currency"])

    label_value(r + 7,  LEFT,  "Country:",        d["country"])

    put(r + 8,  0, sep, colors["dim"])

    # ── Contact / web ────────────────────────────────────────────────────
    label_value(r + 9,  LEFT,  "Phone:",          d["phone"])
    label_value(r + 10, LEFT,  "Website:",        d["website"], val_color=colors["dim"])

    put(r + 11, 0, sep, colors["dim"])
