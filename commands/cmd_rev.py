"""
commands/cmd_rev.py
-------------------
REV <TICKER> — Revenue breakdown by segment and geography.

Data source: Finnhub /stock/revenue-breakdown (Premium endpoint).

Key bindings:
  ← / →   cycle through filing periods (newest → oldest)
"""

import curses

import market_data


_BAR_WIDTH = 22

_AXIS_LABELS = {
    "ProductOrServiceAxis":                      "BY PRODUCT / SERVICE",
    "StatementGeographicalAxis":                 "BY GEOGRAPHY",
    "GeographicalAxis":                          "BY GEOGRAPHY",
    "SegmentReportingInformationBySegmentAxis":  "BY SEGMENT",
    "RevenueTypeAxis":                           "BY REVENUE TYPE",
    "ConcentrationRiskByBenchmarkAxis":          "BY BENCHMARK",
    "BusinessAcquisitionAxis":                   "BY ACQUISITION",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    _, w = stdscr.getmaxyx()
    if col >= w:
        return
    text = str(text)[:max(0, w - col - 1)]
    if not text:
        return
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr)
        stdscr.addstr(row, col, text)
        stdscr.attroff(attr)
    except Exception:
        pass


def _fmt_val(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "N/A"
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:,.0f}"


def _to_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _bar(pct, width: int = _BAR_WIDTH) -> str:
    filled = max(0, min(width, int(round(_to_float(pct) / 100 * width))))
    return "█" * filled + "░" * (width - filled)


def _axis_label(raw: str) -> str:
    short = raw.split(":")[-1]
    return _AXIS_LABELS.get(short, short.replace("Axis", "").strip().upper())


def _primary(filing: dict):
    """Return the breakdown item with the largest value, or None."""
    try:
        items = filing.get("breakdown") or []
        if not items:
            return None
        return max(items, key=lambda x: _to_float(x.get("value")))
    except Exception:
        return None


def _has_data(filing: dict) -> bool:
    """True if this filing has at least one non-zero breakdown entry."""
    try:
        items = filing.get("breakdown") or []
        return any(_to_float(x.get("value")) > 0 for x in items)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    ticker = parts[1] if len(parts) > 1 else None
    if not ticker:
        return {"filings": None, "error": "Usage: REV <TICKER>   e.g. REV AAPL",
                "ticker": None, "period_idx": 0}

    try:
        raw     = market_data.server_get(f"/api/revenue-breakdown/{ticker.upper()}")
        filings = raw.get("data", []) if isinstance(raw, dict) else []
        if not filings:
            return {"filings": None, "period_idx": 0, "ticker": ticker.upper(),
                    "error": (f"No revenue breakdown data for '{ticker}'. "
                              f"Requires Finnhub Premium.")}

        # Auto-advance past filings with no data (most-recent may be unpopulated)
        start_idx = 0
        for i, f in enumerate(filings):
            if _has_data(f):
                start_idx = i
                break

        return {
            "ticker":     ticker.upper(),
            "filings":    filings,
            "period_idx": start_idx,
            "error":      None,
        }
    except Exception as e:
        return {"filings": None, "error": str(e), "ticker": ticker, "period_idx": 0}


# ---------------------------------------------------------------------------
# on_keypress — ← → cycle periods
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    filings = cache.get("filings") or []
    n   = len(filings)
    idx = cache.get("period_idx", 0)

    if key == curses.KEY_LEFT:
        return {**cache, "period_idx": min(n - 1, idx + 1)}   # older

    if key == curses.KEY_RIGHT:
        return {**cache, "period_idx": max(0, idx - 1)}       # newer

    return cache


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    try:
        _render(stdscr, cache, colors)
    except Exception as e:
        try:
            stdscr.addstr(4, 2, f"Render error: {e}"[:60])
        except Exception:
            pass


def _render(stdscr, cache: dict, colors: dict) -> None:
    height, width = stdscr.getmaxyx()
    sep = "  " + "─" * max(0, width - 4)

    def put(row, col, text, color, bold=False):
        _put(stdscr, row, col, text, color, bold)

    error = cache.get("error")
    if error:
        put(4, 0, sep, colors["dim"])
        put(5, 2, "REV", colors["orange"], bold=True)
        put(6, 0, sep, colors["dim"])
        put(7, 2, error, colors["negative"])
        return

    filings = cache.get("filings")
    if not filings:
        put(4, 2, "Loading...", colors["dim"])
        return

    ticker    = cache.get("ticker", "")
    idx       = cache.get("period_idx", 0)
    n_periods = len(filings)
    filing    = filings[idx]

    r = 4

    # ── Header ────────────────────────────────────────────────────────────────
    put(r, 0, sep, colors["dim"]); r += 1
    put(r, 2, "REV", colors["orange"], bold=True)
    put(r, 7, ticker, colors["orange"], bold=True)
    put(r, 7 + len(ticker) + 2, "Revenue Breakdown", colors["dim"])
    nav = f"Period {idx + 1}/{n_periods}  ← →"
    put(r, max(2, width - len(nav) - 2), nav, colors["dim"])
    r += 1
    put(r, 0, sep, colors["dim"]); r += 1

    # ── Primary breakdown item ────────────────────────────────────────────────
    primary = _primary(filing)
    if not primary:
        put(r, 2, "No breakdown data for this period — use ← to navigate.",
            colors["orange"])
        put(r + 2, 0, sep, colors["dim"])
        hint = f"  ← older period   → newer period   ·   {idx + 1} of {n_periods} filings"
        put(height - 1, 0, hint[:width - 1], colors["dim"])
        return

    start_dt  = primary.get("startDate", "N/A")
    end_dt    = primary.get("endDate",   "N/A")
    total_rev = _to_float(primary.get("value", 0))

    put(r, 2, "Period:", colors["dim"])
    put(r, 12, f"{start_dt}  →  {end_dt}", colors["orange"])
    r += 1
    put(r, 2, "Total Revenue:", colors["dim"])
    put(r, 18, _fmt_val(total_rev), colors["orange"], bold=True)
    r += 2

    # ── Segment axes ──────────────────────────────────────────────────────────
    LABEL_W = 24
    VAL_W   = 10

    axes = primary.get("revenueBreakdown") or []

    if not axes:
        put(r, 0, sep, colors["dim"]); r += 1
        put(r, 2, "No segment breakdown available for this period.", colors["orange"])
        r += 1
    else:
        for axis_obj in axes:
            if r >= height - 3:
                break

            items = axis_obj.get("data") or []
            if not items:
                continue

            axis_header = _axis_label(axis_obj.get("axis", ""))

            put(r, 0, sep, colors["dim"]); r += 1
            put(r, 2, axis_header, colors["dim"], bold=True); r += 1

            col_hdr = (f"  {'SEGMENT':<{LABEL_W}}  {'VALUE':>{VAL_W}}"
                       f"  {'':^{_BAR_WIDTH}}  {'SHARE':>6}")
            put(r, 0, col_hdr, colors["dim"], bold=True); r += 1

            sorted_items = sorted(items,
                                  key=lambda x: _to_float(x.get("value")),
                                  reverse=True)

            for item in sorted_items:
                if r >= height - 2:
                    break
                label   = str(item.get("label") or "N/A")
                val_str = _fmt_val(item.get("value", 0))
                pct_f   = _to_float(item.get("percentage", 0))
                bar_str = _bar(pct_f)
                pct_str = f"{pct_f:5.1f}%"
                color   = colors["orange"] if pct_f >= 20 else colors["dim"]

                line = (f"  {label:<{LABEL_W}}  {val_str:>{VAL_W}}"
                        f"  {bar_str}  {pct_str}")
                put(r, 0, line, color)
                r += 1

            r += 1  # gap between axes

    # ── Footer hint ───────────────────────────────────────────────────────────
    if r < height - 1:
        put(r, 0, sep, colors["dim"])
    hint = f"  ← older period   → newer period   ·   {idx + 1} of {n_periods} filings"
    put(height - 1, 0, hint[:width - 1], colors["dim"])
