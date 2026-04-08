"""
commands/cmd_ipo.py
-------------------
Implements the  IPO  command — IPO Calendar.

Displays recent and upcoming IPOs sourced from Finnhub's /calendar/ipo endpoint.
Defaults to a window of -30 days to +90 days from today.

Usage:
  IPO                        <- default window (past 30d + next 90d)
  IPO <from> <to>            <- custom date range  e.g. IPO 2025-01-01 2025-06-30

Navigation (PANE mode):
  ↑ / ↓     move cursor through IPO list
  Enter      expand selected IPO for full details / collapse
  ← / →     cycle filter: ALL → PRICED → EXPECTED → FILED → WITHDRAWN
"""

import curses
import datetime

import market_data

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATUS_CYCLE  = ["ALL", "priced", "expected", "filed", "withdrawn"]
STATUS_LABELS = {
    "priced":    ("PRICED",    "positive"),
    "expected":  ("EXPECTED",  "orange"),
    "filed":     ("FILED",     "dim"),
    "withdrawn": ("WITHDRAWN", "negative"),
}

_DEFAULT_BACK_DAYS    = 30
_DEFAULT_FORWARD_DAYS = 90
_VISIBLE_ROWS         = 18   # max list rows before scrolling


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_range() -> tuple[str, str]:
    today = datetime.date.today()
    frm   = (today - datetime.timedelta(days=_DEFAULT_BACK_DAYS)).strftime("%Y-%m-%d")
    to    = (today + datetime.timedelta(days=_DEFAULT_FORWARD_DAYS)).strftime("%Y-%m-%d")
    return frm, to


def _fmt_shares(val) -> str:
    try:
        n = float(val)
        if n <= 0:
            return "N/A"
        if n >= 1_000_000_000:
            return f"{n / 1_000_000_000:.2f}B"
        if n >= 1_000_000:
            return f"{n / 1_000_000:.2f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(int(n))
    except (TypeError, ValueError):
        return "N/A"


def _fmt_value(val) -> str:
    try:
        n = float(val)
        if n <= 0:
            return "N/A"
        if n >= 1_000_000_000:
            return f"${n / 1_000_000_000:.2f}B"
        if n >= 1_000_000:
            return f"${n / 1_000_000:.2f}M"
        return f"${n:,.0f}"
    except (TypeError, ValueError):
        return "N/A"


def _status_color(status: str, colors: dict):
    info = STATUS_LABELS.get((status or "").lower())
    if info:
        return colors[info[1]]
    return colors["dim"]


def _status_label(status: str) -> str:
    info = STATUS_LABELS.get((status or "").lower())
    return info[0] if info else (status or "N/A").upper()


def _filter_ipos(ipos: list, status_filter: str) -> list:
    if status_filter == "ALL":
        return ipos
    return [x for x in ipos if (x.get("status") or "").lower() == status_filter]


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    # Parse optional date args
    if len(parts) >= 3:
        frm, to = parts[1], parts[2]
    else:
        frm, to = _default_range()

    try:
        raw  = market_data.server_get("/api/ipo", params={"frm": frm, "to": to})
        ipos = raw.get("ipoCalendar", []) if isinstance(raw, dict) else []

        # Sort: upcoming first, then by date descending for past
        today = datetime.date.today().isoformat()
        upcoming = sorted([x for x in ipos if (x.get("date") or "") >= today],
                          key=lambda x: x.get("date", ""))
        past     = sorted([x for x in ipos if (x.get("date") or "") < today],
                          key=lambda x: x.get("date", ""), reverse=True)
        sorted_ipos = upcoming + past

        return {
            "error":         None,
            "ipos":          sorted_ipos,
            "from":          frm,
            "to":            to,
            "selected":      0,
            "expanded":      False,
            "status_filter": "ALL",
            "page_offset":   0,
        }
    except Exception as e:
        return {
            "error": str(e), "ipos": [], "from": frm, "to": to,
            "selected": 0, "expanded": False, "status_filter": "ALL",
            "page_offset": 0,
        }


# ---------------------------------------------------------------------------
# on_keypress — ↑↓ navigate, Enter expand/collapse, ←→ cycle filter
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    ipos          = cache.get("ipos", [])
    status_filter = cache.get("status_filter", "ALL")
    filtered      = _filter_ipos(ipos, status_filter)
    n             = len(filtered)
    selected      = cache.get("selected", 0)
    expanded      = cache.get("expanded", False)
    page_offset   = cache.get("page_offset", 0)

    # ← / → — cycle status filter
    if key == curses.KEY_RIGHT:
        idx        = STATUS_CYCLE.index(status_filter) if status_filter in STATUS_CYCLE else 0
        new_filter = STATUS_CYCLE[(idx + 1) % len(STATUS_CYCLE)]
        return {**cache, "status_filter": new_filter, "selected": 0,
                "page_offset": 0, "expanded": False}

    if key == curses.KEY_LEFT:
        idx        = STATUS_CYCLE.index(status_filter) if status_filter in STATUS_CYCLE else 0
        new_filter = STATUS_CYCLE[(idx - 1) % len(STATUS_CYCLE)]
        return {**cache, "status_filter": new_filter, "selected": 0,
                "page_offset": 0, "expanded": False}

    # ↑ / ↓ — move cursor
    if key == curses.KEY_UP:
        if n == 0:
            return cache
        new_sel = max(0, selected - 1)
        # Scroll page up if cursor goes above visible window
        new_off = min(page_offset, new_sel)
        return {**cache, "selected": new_sel, "page_offset": new_off, "expanded": False}

    if key == curses.KEY_DOWN:
        if n == 0:
            return cache
        new_sel = min(n - 1, selected + 1)
        # Scroll page down if cursor goes below visible window
        new_off = page_offset
        if new_sel >= page_offset + _VISIBLE_ROWS:
            new_off = new_sel - _VISIBLE_ROWS + 1
        return {**cache, "selected": new_sel, "page_offset": new_off, "expanded": False}

    # Enter — toggle expand on selected row
    if key in (curses.KEY_ENTER, 10, 13):
        return {**cache, "expanded": not expanded}

    return cache


# ---------------------------------------------------------------------------
# render helpers
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr)
        stdscr.addstr(row, col, text)
        stdscr.attroff(attr)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    _, width = stdscr.getmaxyx()

    error = cache.get("error")
    if error:
        _put(stdscr, 4, 0, f"  Error: {error}", colors["negative"])
        return

    ipos          = cache.get("ipos", [])
    status_filter = cache.get("status_filter", "ALL")
    filtered      = _filter_ipos(ipos, status_filter)
    selected      = cache.get("selected", 0)
    expanded      = cache.get("expanded", False)
    page_offset   = cache.get("page_offset", 0)
    frm           = cache.get("from", "")
    to            = cache.get("to", "")

    if not ipos and ipos is not None:
        _put(stdscr, 4, 0, "  Loading...", colors["dim"])
        return

    sep = f"  {'─' * (width - 3)}"
    r   = 4

    # ── Header ───────────────────────────────────────────────────────────
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, "IPO CALENDAR", colors["orange"], bold=True)
    _put(stdscr, r, 16, f"{frm}  →  {to}", colors["dim"])
    _put(stdscr, r, 40, f"  {len(ipos)} total  |  {len(filtered)} shown", colors["dim"])
    r += 1

    # Filter tab bar
    col = 2
    for tab in STATUS_CYCLE:
        label = f"[ {tab} ]" if tab == status_filter else f"  {tab}  "
        bold  = tab == status_filter
        clr   = colors["orange"] if bold else colors["dim"]
        _put(stdscr, r, col, label, clr, bold=bold)
        col += len(label) + 1
    _put(stdscr, r, col + 1, "← → filter   ↑ ↓ navigate   Enter expand", colors["dim"])
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    if not filtered:
        _put(stdscr, r, 2, f"No IPOs with status '{status_filter}'.", colors["dim"])
        return

    # ── If a row is expanded, show detail panel instead of the list ──────
    if expanded and 0 <= selected < len(filtered):
        ipo = filtered[selected]
        _render_detail(stdscr, r, ipo, colors, sep)
        return

    # ── Column headers ────────────────────────────────────────────────────
    C_DATE   = 2
    C_NAME   = 14
    C_SYM    = 46
    C_SHARES = 54
    C_PRICE  = 66
    C_STATUS = 77

    _put(stdscr, r, C_DATE,   f"{'DATE':<11}",   colors["dim"], bold=True)
    _put(stdscr, r, C_NAME,   f"{'COMPANY':<31}", colors["dim"], bold=True)
    _put(stdscr, r, C_SYM,    f"{'SYM':<7}",     colors["dim"], bold=True)
    _put(stdscr, r, C_SHARES, f"{'SHARES':<11}",  colors["dim"], bold=True)
    _put(stdscr, r, C_PRICE,  f"{'PRICE':<10}",   colors["dim"], bold=True)
    if C_STATUS < width - 4:
        _put(stdscr, r, C_STATUS, "STATUS", colors["dim"], bold=True)
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    # ── IPO rows ──────────────────────────────────────────────────────────
    visible = filtered[page_offset: page_offset + _VISIBLE_ROWS]
    for i, ipo in enumerate(visible):
        abs_idx = page_offset + i
        is_sel  = abs_idx == selected

        date    = (ipo.get("date") or "N/A")[:10]
        name    = (ipo.get("name") or "N/A")[:30]
        sym     = (ipo.get("symbol") or "—")[:6]
        shares  = _fmt_shares(ipo.get("numberOfShares"))
        price   = (str(ipo.get("price") or "N/A"))[:9]
        status  = ipo.get("status", "")
        s_label = _status_label(status)[:9]

        if is_sel:
            # Fill entire row with highlight background
            try:
                stdscr.attron(colors["highlight"] | curses.A_BOLD)
                stdscr.addstr(r, 0, " " * (width - 1))
                stdscr.attroff(colors["highlight"] | curses.A_BOLD)
            except Exception:
                pass
            row_attr = colors["highlight"] | curses.A_BOLD

            def _row_put(col, text):
                try:
                    stdscr.attron(row_attr)
                    stdscr.addstr(r, col, text)
                    stdscr.attroff(row_attr)
                except Exception:
                    pass

            _row_put(C_DATE,   f"{date:<11}")
            _row_put(C_NAME,   f"{name:<31}")
            _row_put(C_SYM,    f"{sym:<7}")
            _row_put(C_SHARES, f"{shares:<11}")
            _row_put(C_PRICE,  f"{price:<10}")
            if C_STATUS < width - 4:
                _row_put(C_STATUS, s_label)
        else:
            # Normal row: white on black, status colored
            _put(stdscr, r, C_DATE,   f"{date:<11}",   colors["dim"])
            _put(stdscr, r, C_NAME,   f"{name:<31}",   colors["dim"])
            _put(stdscr, r, C_SYM,    f"{sym:<7}",     colors["dim"])
            _put(stdscr, r, C_SHARES, f"{shares:<11}", colors["dim"])
            _put(stdscr, r, C_PRICE,  f"{price:<10}",  colors["dim"])
            if C_STATUS < width - 4:
                _put(stdscr, r, C_STATUS, s_label, _status_color(status, colors))

        r += 1

    # Scroll indicator
    total = len(filtered)
    if total > _VISIBLE_ROWS:
        shown_end = min(page_offset + _VISIBLE_ROWS, total)
        _put(stdscr, r, 0, sep, colors["dim"]); r += 1
        _put(stdscr, r, 2,
             f"Showing {page_offset + 1}–{shown_end} of {total}   ↑ ↓ to scroll",
             colors["dim"])
    else:
        _put(stdscr, r, 0, sep, colors["dim"])


def _render_detail(stdscr, r: int, ipo: dict, colors: dict, sep: str) -> None:
    """Render expanded detail view for a single IPO."""

    def lv(row, label, value, val_color=None):
        _put(stdscr, row, 2,  f"{label:<24}", colors["dim"])
        _put(stdscr, row, 26, str(value),     val_color or colors["orange"])

    status       = ipo.get("status", "")
    name         = ipo.get("name", "N/A")
    symbol       = ipo.get("symbol", "N/A")
    date         = ipo.get("date", "N/A")
    exchange     = ipo.get("exchange", "N/A")
    price        = ipo.get("price", "N/A")
    shares       = _fmt_shares(ipo.get("numberOfShares"))
    total_val    = _fmt_value(ipo.get("totalSharesValue"))
    s_label      = _status_label(status)
    s_color      = colors.get("positive") if status == "priced" else \
                   (colors.get("negative") if status == "withdrawn" else colors.get("orange"))

    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, f"{symbol}", colors["orange"], bold=True)
    _put(stdscr, r, 2 + len(symbol) + 2, name, colors["header"], bold=True)
    r += 1
    _put(stdscr, r, 2, f"Status: ", colors["dim"])
    _put(stdscr, r, 10, s_label, s_color, bold=True)
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    lv(r, "IPO Date:",             date);                               r += 1
    lv(r, "Exchange:",             exchange);                           r += 1
    lv(r, "Offer Price:",          price);                              r += 1
    lv(r, "Shares Offered:",       shares);                            r += 1
    lv(r, "Total Offering Value:", total_val, colors.get("positive")); r += 1

    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, "Press Enter to collapse", colors["dim"])
