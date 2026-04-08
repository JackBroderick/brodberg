"""
commands/cmd_earn.py
--------------------
Implements the  EARN  command — Next-Day Earnings Calendar.

Data is scraped from Barchart by the server once daily at ~4:05 PM ET
(stocks.optionable.upcoming_earnings.1d.us list).

  EARN      <- show next trading day's earnings reporters

Navigation (PANE mode):
  ↑ / ↓    move cursor through rows
  ← / →    filter: ALL → BMO → AMC
  Enter     expand selected row / collapse
"""

import curses

import market_data

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VISIBLE_ROWS = 18
_FILTER_CYCLE = ["ALL", "BMO", "AMC"]


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

def _get(row: dict, *keys) -> str:
    for k in keys:
        v = row.get(k, "")
        if v:
            return str(v).strip()
    return ""

def _sym(r):       return _get(r, "Symbol")
def _name(r):      return _get(r, "Name")
def _date(r):      return _get(r, "Date")
def _time(r):      return _get(r, "Time")        # BMO / AMC
def _price(r):     return _get(r, "Price")
def _chg(r):       return _get(r, "Change %")
def _ivrank(r):    return _get(r, "IV Rank")
def _implmv(r):    return _get(r, "Impl Move")
def _implmvp(r):   return _get(r, "Impl Move %")
def _optvol(r):    return _get(r, "Opt Volume")
def _lasttrd(r):   return _get(r, "Last Trade")


def _filter_rows(rows: list, filt: str) -> list:
    if filt == "ALL":
        return rows
    return [r for r in rows if _time(r).upper() == filt]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr)
        stdscr.addstr(row, col, text)
        stdscr.attroff(attr)
    except Exception:
        pass


def _chg_color(row: dict, colors: dict):
    try:
        v = float(_chg(row))
        return colors.get("positive") if v > 0 else colors.get("negative") if v < 0 else colors["dim"]
    except Exception:
        return colors["dim"]


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    try:
        raw   = market_data.server_get("/api/earnings")
        data  = raw.get("data", []) if isinstance(raw, dict) else []
        as_of = raw.get("as_of")    if isinstance(raw, dict) else None
        return {
            "error":       None,
            "rows":        data,
            "as_of":       as_of,
            "selected":    0,
            "expanded":    False,
            "page_offset": 0,
            "filter":      "ALL",
        }
    except Exception as e:
        return {
            "error": str(e), "rows": [], "as_of": None,
            "selected": 0, "expanded": False, "page_offset": 0, "filter": "ALL",
        }


# ---------------------------------------------------------------------------
# on_keypress — ↑↓ navigate, ←→ filter, Enter expand/collapse
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    filt = cache.get("filter", "ALL")
    rows = _filter_rows(cache.get("rows", []), filt)
    n    = len(rows)
    sel  = cache.get("selected", 0)
    exp  = cache.get("expanded", False)
    off  = cache.get("page_offset", 0)

    if key == curses.KEY_LEFT:
        idx = _FILTER_CYCLE.index(filt) if filt in _FILTER_CYCLE else 0
        return {**cache, "filter": _FILTER_CYCLE[(idx - 1) % len(_FILTER_CYCLE)],
                "selected": 0, "page_offset": 0, "expanded": False}

    if key == curses.KEY_RIGHT:
        idx = _FILTER_CYCLE.index(filt) if filt in _FILTER_CYCLE else 0
        return {**cache, "filter": _FILTER_CYCLE[(idx + 1) % len(_FILTER_CYCLE)],
                "selected": 0, "page_offset": 0, "expanded": False}

    if key == curses.KEY_UP:
        if n == 0:
            return cache
        new_sel = max(0, sel - 1)
        return {**cache, "selected": new_sel,
                "page_offset": min(off, new_sel), "expanded": False}

    if key == curses.KEY_DOWN:
        if n == 0:
            return cache
        new_sel = min(n - 1, sel + 1)
        new_off = off
        if new_sel >= off + _VISIBLE_ROWS:
            new_off = new_sel - _VISIBLE_ROWS + 1
        return {**cache, "selected": new_sel, "page_offset": new_off, "expanded": False}

    if key in (curses.KEY_ENTER, 10, 13):
        return {**cache, "expanded": not exp}

    return cache


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    _, width = stdscr.getmaxyx()
    sep      = f"  {'─' * (width - 3)}"
    r        = 4

    error = cache.get("error")
    if error:
        _put(stdscr, r, 0, f"  Error: {error}", colors["negative"])
        return

    all_rows = cache.get("rows", [])
    filt     = cache.get("filter", "ALL")
    rows     = _filter_rows(all_rows, filt)
    sel      = cache.get("selected", 0)
    exp      = cache.get("expanded", False)
    off      = cache.get("page_offset", 0)
    as_of    = cache.get("as_of") or "—"

    # ── Header ────────────────────────────────────────────────────────────
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, "EARNINGS CALENDAR", colors["orange"], bold=True)
    _put(stdscr, r, 22, f"as of {as_of}", colors["dim"])
    if all_rows:
        _put(stdscr, r, 40,
             f"{len(all_rows)} total  |  {len(rows)} shown", colors["dim"])
    r += 1

    # Filter tabs
    col = 2
    for tab in _FILTER_CYCLE:
        label = f"[{tab}]" if tab == filt else f" {tab} "
        _put(stdscr, r, col, label,
             colors["orange"] if tab == filt else colors["dim"],
             bold=(tab == filt))
        col += len(label) + 1
    _put(stdscr, r, col + 1, "← → filter   ↑ ↓ navigate   Enter expand",
         colors["dim"])
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    if not all_rows:
        _put(stdscr, r, 2,
             "No data yet — server scrapes Barchart daily at 4:05 PM ET.",
             colors["dim"])
        return

    if not rows:
        _put(stdscr, r, 2, f"No {filt} reporters in today's data.", colors["dim"])
        return

    # ── Expanded detail view ──────────────────────────────────────────────
    if exp and 0 <= sel < len(rows):
        _render_detail(stdscr, r, rows[sel], colors, sep, width)
        return

    # ── Column layout ─────────────────────────────────────────────────────
    C_SYM   = 2
    C_TIME  = 10   # BMO / AMC
    C_NAME  = 16   # company name (dynamic width)
    C_DATE  = max(38, width - 58)
    C_PRICE = C_DATE  + 12
    C_CHG   = C_PRICE + 10
    C_IVRK  = C_CHG   + 10
    C_IMPL  = C_IVRK  + 10

    name_w  = C_DATE - C_NAME - 2

    _put(stdscr, r, C_SYM,  f"{'SYM':<7}",              colors["dim"], bold=True)
    _put(stdscr, r, C_TIME, f"{'TIME':<5}",              colors["dim"], bold=True)
    _put(stdscr, r, C_NAME, f"{'COMPANY':<{name_w}}",    colors["dim"], bold=True)
    _put(stdscr, r, C_DATE, f"{'DATE':<11}",             colors["dim"], bold=True)
    _put(stdscr, r, C_PRICE,f"{'PRICE':<9}",             colors["dim"], bold=True)
    _put(stdscr, r, C_CHG,  f"{'CHG %':<9}",             colors["dim"], bold=True)
    _put(stdscr, r, C_IVRK, f"{'IV RANK':<9}",           colors["dim"], bold=True)
    if C_IMPL < width - 4:
        _put(stdscr, r, C_IMPL, f"{'IMPL MV%':<9}",      colors["dim"], bold=True)
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    # ── Data rows ─────────────────────────────────────────────────────────
    visible = rows[off: off + _VISIBLE_ROWS]

    for i, row_data in enumerate(visible):
        abs_idx = off + i
        is_sel  = abs_idx == sel

        sym    = _sym(row_data)[:7]
        tc     = _time(row_data)[:5]
        name   = _name(row_data)[:name_w]
        date   = _date(row_data)[:11]
        price  = _price(row_data)[:9]
        chg    = _chg(row_data)[:9]
        ivrk   = _ivrank(row_data)[:9]
        impl   = _implmvp(row_data)[:9]

        time_color = (colors.get("positive") if tc == "BMO"
                      else colors.get("negative") if tc == "AMC"
                      else colors["dim"])
        chg_col    = _chg_color(row_data, colors)

        if is_sel:
            _put(stdscr, r, C_SYM,   f"{sym:<7}",        colors["orange"], bold=True)
            _put(stdscr, r, C_TIME,  f"{tc:<5}",          colors["orange"], bold=True)
            _put(stdscr, r, C_NAME,  f"{name:<{name_w}}", colors["orange"], bold=True)
            _put(stdscr, r, C_DATE,  f"{date:<11}",       colors["orange"], bold=True)
            _put(stdscr, r, C_PRICE, f"{price:<9}",       colors["orange"], bold=True)
            _put(stdscr, r, C_CHG,   f"{chg:<9}",         colors["orange"], bold=True)
            _put(stdscr, r, C_IVRK,  f"{ivrk:<9}",        colors["orange"], bold=True)
            if C_IMPL < width - 4:
                _put(stdscr, r, C_IMPL, f"{impl:<9}",     colors["orange"], bold=True)
        else:
            _put(stdscr, r, C_SYM,   f"{sym:<7}",         colors["orange"])
            _put(stdscr, r, C_TIME,  f"{tc:<5}",           time_color, bold=True)
            _put(stdscr, r, C_NAME,  f"{name:<{name_w}}",  colors["dim"])
            _put(stdscr, r, C_DATE,  f"{date:<11}",        colors["dim"])
            _put(stdscr, r, C_PRICE, f"{price:<9}",        colors["dim"])
            _put(stdscr, r, C_CHG,   f"{chg:<9}",          chg_col)
            _put(stdscr, r, C_IVRK,  f"{ivrk:<9}",         colors["dim"])
            if C_IMPL < width - 4:
                _put(stdscr, r, C_IMPL, f"{impl:<9}",      colors["dim"])

        r += 1

    # Scroll indicator
    total = len(rows)
    if total > _VISIBLE_ROWS:
        shown_end = min(off + _VISIBLE_ROWS, total)
        _put(stdscr, r, 0, sep, colors["dim"]); r += 1
        _put(stdscr, r, 2,
             f"Showing {off + 1}–{shown_end} of {total}   ↑ ↓ to scroll",
             colors["dim"])
    else:
        _put(stdscr, r, 0, sep, colors["dim"])


def _render_detail(stdscr, r: int, row_data: dict,
                   colors: dict, sep: str, width: int) -> None:
    def lv(row, label, value, val_color=None):
        _put(stdscr, row, 2,  f"{label:<22}", colors["dim"])
        _put(stdscr, row, 24, str(value),     val_color or colors["orange"])

    sym    = _sym(row_data)
    name   = _name(row_data)
    date   = _date(row_data)
    tc     = _time(row_data)
    price  = _price(row_data)
    chg    = _chg(row_data)
    ivrk   = _ivrank(row_data)
    impl   = _implmv(row_data)
    implp  = _implmvp(row_data)
    optvol = _optvol(row_data)
    last   = _lasttrd(row_data)

    time_color = (colors.get("positive") if tc == "BMO"
                  else colors.get("negative") if tc == "AMC"
                  else colors["dim"])
    chg_col    = _chg_color(row_data, colors)

    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, sym, colors["orange"], bold=True)
    if name:
        _put(stdscr, r, 2 + len(sym) + 2, name[:width - len(sym) - 10],
             colors["orange"], bold=True)
    r += 1
    _put(stdscr, r, 2, tc, time_color, bold=True); r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    lv(r, "Earnings Date:",      date);                    r += 1
    lv(r, "Report Time:",        tc,    time_color);       r += 1
    lv(r, "Stock Price:",        price);                   r += 1
    lv(r, "Change %:",           chg,   chg_col);          r += 1
    lv(r, "IV Rank (1yr):",      ivrk);                   r += 1
    lv(r, "Implied Move $:",     impl);                    r += 1
    lv(r, "Implied Move %:",     implp);                   r += 1
    lv(r, "Options Volume:",     optvol);                  r += 1
    lv(r, "Last Trade:",         last);                    r += 1

    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, "Press Enter to collapse", colors["dim"])
