"""
commands/cmd_sent.py
--------------------
Implements the  SENT <TICKER>  command (News Sentiment).

Displays Finnhub's NLP-derived news sentiment score, bull/bear breakdown,
buzz metrics, and sector comparison for the requested ticker.

Usage:
  SENT AAPL
  SENT TSLA
"""

import curses

import market_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(pct: float, width: int = 30) -> str:
    """Build a filled block bar string for a 0.0–1.0 value."""
    try:
        filled = max(0, min(width, round(pct * width)))
    except (TypeError, ValueError):
        filled = 0
    return "\u2588" * filled + "\u2591" * (width - filled)


def _pct_str(val) -> str:
    try:
        return f"{float(val) * 100:.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _score_str(val) -> str:
    try:
        return f"{float(val):.3f}"
    except (TypeError, ValueError):
        return "N/A"


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    ticker = parts[1] if len(parts) > 1 else None
    if not ticker:
        return {"data": None, "error": "Usage: SENT <TICKER>   e.g. SENT AAPL"}

    try:
        raw = market_data.server_get(f"/api/sentiment/{ticker.upper()}")
        if not raw or not isinstance(raw, dict) or not raw.get("sentiment"):
            return {"data": None, "error": f"No sentiment data found for '{ticker}'."}

        sentiment = raw.get("sentiment", {})
        buzz      = raw.get("buzz", {})

        data = {
            "symbol":              ticker.upper(),
            "bull_pct":            sentiment.get("bullishPercent", 0),
            "bear_pct":            sentiment.get("bearishPercent", 0),
            "company_score":       raw.get("companyNewsScore", 0),
            "sector_score":        raw.get("sectorAverageNewsScore", 0),
            "sector_bull":         raw.get("sectorAverageBullishPercent", 0),
            "articles_this_week":  buzz.get("articlesInLastWeek", 0),
            "weekly_average":      buzz.get("weeklyAverage", 0),
            "buzz_ratio":          buzz.get("buzz", 0),
        }
        return {"data": data, "error": None}
    except Exception as e:
        return {"data": None, "error": str(e)}


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
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

    def put(row, col, text, color, bold=False):
        attr = color | (curses.A_BOLD if bold else 0)
        try:
            stdscr.attron(attr)
            stdscr.addstr(row, col, text)
            stdscr.attroff(attr)
        except Exception:
            pass

    symbol      = d["symbol"]
    bull_pct    = d["bull_pct"]
    bear_pct    = d["bear_pct"]
    co_score    = d["company_score"]
    sec_score   = d["sector_score"]
    sec_bull    = d["sector_bull"]
    articles    = d["articles_this_week"]
    avg         = d["weekly_average"]
    buzz_ratio  = d["buzz_ratio"]

    sep = f"  {'─' * 50}"

    r = 4
    put(r,     0, sep,                        colors["dim"])
    put(r + 1, 0, f"   {symbol}",             colors["orange"], bold=True)
    put(r + 1, 8, "  News Sentiment",         colors["header"], bold=True)
    put(r + 2, 0, "   Powered by Finnhub NLP", colors["dim"])
    put(r + 3, 0, sep,                        colors["dim"])

    row = r + 4

    # ── Sentiment bars ───────────────────────────────────────────────────
    try:
        bull_f = float(bull_pct)
        bear_f = float(bear_pct)
    except (TypeError, ValueError):
        bull_f = bear_f = 0.0

    bar_bull = _bar(bull_f, 30)
    bar_bear = _bar(bear_f, 30)

    put(row,     2, f"{'Bullish':<12}", colors["dim"])
    put(row,     14, f"{_pct_str(bull_f):<7}", colors["positive"], bold=True)
    put(row,     22, bar_bull, colors["positive"])
    row += 1

    put(row,     2, f"{'Bearish':<12}", colors["dim"])
    put(row,     14, f"{_pct_str(bear_f):<7}", colors["negative"], bold=True)
    put(row,     22, bar_bear, colors["negative"])
    row += 2

    put(row, 0, sep, colors["dim"])
    row += 1

    # ── Scores ──────────────────────────────────────────────────────────
    def label_value(r, label, value, val_color):
        put(r, 2,  f"{label:<26}", colors["dim"])
        put(r, 28, value,          val_color)

    def score_color(score):
        try:
            s = float(score)
            if s >= 0.6:
                return colors["positive"]
            if s <= 0.4:
                return colors["negative"]
        except (TypeError, ValueError):
            pass
        return colors["orange"]

    label_value(row, "Company News Score:",      _score_str(co_score),  score_color(co_score))
    row += 1
    label_value(row, "Sector Avg Score:",        _score_str(sec_score), score_color(sec_score))
    row += 1
    label_value(row, "Sector Avg Bullish:",      _pct_str(sec_bull),    colors["orange"])
    row += 2

    put(row, 0, sep, colors["dim"])
    row += 1

    # ── Buzz ─────────────────────────────────────────────────────────────
    put(row, 2, "BUZZ METRICS", colors["header"], bold=True)
    row += 1

    label_value(row, "Articles This Week:",      str(int(articles)),          colors["orange"])
    row += 1
    label_value(row, "Weekly Average:",          f"{float(avg):.1f}" if avg else "N/A", colors["dim"])
    row += 1

    try:
        buzz_f = float(buzz_ratio)
        buzz_color = colors["positive"] if buzz_f >= 1.0 else colors["dim"]
        buzz_str   = f"{buzz_f:.2f}x"
    except (TypeError, ValueError):
        buzz_color = colors["dim"]
        buzz_str   = "N/A"

    label_value(row, "Buzz Ratio vs Average:", buzz_str, buzz_color)
    row += 2

    put(row, 0, sep, colors["dim"])
