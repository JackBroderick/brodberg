"""
commands/cmd_uo.py
------------------
Implements the  UO  command — Unusual Options Activity.

Data is scraped from Barchart by the server once daily at ~4:05 PM ET
and stored in the database so it survives server restarts.

  UO        <- show most recent unusual options table

Navigation (PANE mode):
  ↑ / ↓    move cursor through rows
  ← / →    filter: ALL → CALL → PUT
  Enter     expand selected row / collapse
"""

import curses

import market_data

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VISIBLE_ROWS = 18
_FILTER_CYCLE = ["ALL", "CALL", "PUT"]


# ---------------------------------------------------------------------------
# Field access — Barchart CSV headers can vary slightly; try all known names
# ---------------------------------------------------------------------------

def _get(row: dict, *keys) -> str:
    for k in keys:
        v = row.get(k, "")
        if v:
            return str(v).strip()
    return ""


def _sym(r):    return _get(r, "Symbol")
def _name(r):   return _get(r, "Name", "Company", "Description")
def _type(r):   return _get(r, "Put/Call", "Type", "Option Type").upper()
def _exp(r):    return _get(r, "Expiration Date", "Exp Date", "Expiration")
def _strike(r): return _get(r, "Strike", "Strike Price")
def _vol(r):    return _get(r, "Volume")
def _oi(r):     return _get(r, "Open Interest")
def _voloi(r):  return _get(r, "Vol/OI Ratio", "Vol/OI")
def _iv(r):     return _get(r, "IV", "IV %", "Implied Volatility")
def _bid(r):    return _get(r, "Bid")
def _ask(r):    return _get(r, "Ask")
def _last(r):   return _get(r, "Last Price", "Last")
def _time(r):   return _get(r, "Time")


def _type_short(row: dict) -> str:
    t = _type(row)
    if "CALL" in t:
        return "CALL"
    if "PUT" in t:
        return "PUT"
    return t[:4]


def _filter_rows(rows: list, filt: str) -> list:
    if filt == "ALL":
        return rows
    return [r for r in rows if _type_short(r) == filt]


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


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    try:
        raw   = market_data.server_get("/api/unusual-options")
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
        new_off = min(off, new_sel)
        return {**cache, "selected": new_sel, "page_offset": new_off, "expanded": False}

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
    sep      = f"  {'─' * min(88, width - 3)}"
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
    _put(stdscr, r, 2, "UNUSUAL OPTIONS", colors["orange"], bold=True)
    _put(stdscr, r, 19, f"as of {as_of}", colors["dim"])
    if all_rows:
        _put(stdscr, r, 34,
             f"  {len(all_rows)} total  |  {len(rows)} shown", colors["dim"])
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
        _put(stdscr, r, 2, f"No {filt} contracts in today's data.", colors["dim"])
        return

    # ── Expanded detail view ──────────────────────────────────────────────
    if exp and 0 <= sel < len(rows):
        _render_detail(stdscr, r, rows[sel], colors, sep, width)
        return

    # ── Column headers ────────────────────────────────────────────────────
    C_SYM   = 2
    C_TYPE  = 10
    C_STR   = 16
    C_EXP   = 24
    C_VOL   = 36
    C_OI    = 46
    C_VOLOI = 56
    C_IV    = 64

    _put(stdscr, r, C_SYM,   f"{'SYM':<7}",    colors["dim"], bold=True)
    _put(stdscr, r, C_TYPE,  f"{'TYPE':<5}",    colors["dim"], bold=True)
    _put(stdscr, r, C_STR,   f"{'STRIKE':<7}",  colors["dim"], bold=True)
    _put(stdscr, r, C_EXP,   f"{'EXPIRATION':<11}", colors["dim"], bold=True)
    _put(stdscr, r, C_VOL,   f"{'VOLUME':<9}",  colors["dim"], bold=True)
    _put(stdscr, r, C_OI,    f"{'OI':<9}",      colors["dim"], bold=True)
    _put(stdscr, r, C_VOLOI, f"{'VOL/OI':<7}",  colors["dim"], bold=True)
    if C_IV < width - 4:
        _put(stdscr, r, C_IV, f"{'IV':<8}",     colors["dim"], bold=True)
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    # ── Data rows ─────────────────────────────────────────────────────────
    visible = rows[off: off + _VISIBLE_ROWS]

    for i, row_data in enumerate(visible):
        abs_idx = off + i
        is_sel  = abs_idx == sel

        sym   = _sym(row_data)[:7]
        typ   = _type_short(row_data)[:5]
        strike = _strike(row_data)[:7]
        expiry = _exp(row_data)[:11]
        vol    = _vol(row_data)[:9]
        oi     = _oi(row_data)[:9]
        voi    = _voloi(row_data)[:7]
        iv     = _iv(row_data)[:8]

        type_color = (colors.get("positive") if typ == "CALL"
                      else colors.get("negative") if typ == "PUT"
                      else colors["dim"])

        if is_sel:
            try:
                stdscr.attron(colors["highlight"] | curses.A_BOLD)
                stdscr.addstr(r, 0, " " * min(width - 1, 92))
                stdscr.attroff(colors["highlight"] | curses.A_BOLD)
            except Exception:
                pass
            hl = colors["highlight"] | curses.A_BOLD

            def _rp(col, text, _hl=hl):
                try:
                    stdscr.attron(_hl)
                    stdscr.addstr(r, col, text)
                    stdscr.attroff(_hl)
                except Exception:
                    pass

            _rp(C_SYM,   f"{sym:<7}")
            _rp(C_TYPE,  f"{typ:<5}")
            _rp(C_STR,   f"{strike:<7}")
            _rp(C_EXP,   f"{expiry:<11}")
            _rp(C_VOL,   f"{vol:<9}")
            _rp(C_OI,    f"{oi:<9}")
            _rp(C_VOLOI, f"{voi:<7}")
            if C_IV < width - 4:
                _rp(C_IV, f"{iv:<8}")
        else:
            _put(stdscr, r, C_SYM,   f"{sym:<7}",    colors["orange"])
            _put(stdscr, r, C_TYPE,  f"{typ:<5}",    type_color)
            _put(stdscr, r, C_STR,   f"{strike:<7}", colors["dim"])
            _put(stdscr, r, C_EXP,   f"{expiry:<11}", colors["dim"])
            _put(stdscr, r, C_VOL,   f"{vol:<9}",    colors["dim"])
            _put(stdscr, r, C_OI,    f"{oi:<9}",     colors["dim"])
            _put(stdscr, r, C_VOLOI, f"{voi:<7}",    colors["dim"])
            if C_IV < width - 4:
                _put(stdscr, r, C_IV, f"{iv:<8}",    colors["dim"])

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
    """Expanded view for a single unusual options contract."""

    def lv(row, label, value, val_color=None):
        _put(stdscr, row, 2,  f"{label:<20}", colors["dim"])
        _put(stdscr, row, 22, str(value),     val_color or colors["orange"])

    sym    = _sym(row_data)
    name   = _name(row_data)
    typ    = _type_short(row_data)
    expiry = _exp(row_data)
    strike = _strike(row_data)
    vol    = _vol(row_data)
    oi     = _oi(row_data)
    voi    = _voloi(row_data)
    iv     = _iv(row_data)
    bid    = _bid(row_data)
    ask    = _ask(row_data)
    last   = _last(row_data)
    time_  = _time(row_data)

    type_color = (colors.get("positive") if typ == "CALL"
                  else colors.get("negative") if typ == "PUT"
                  else colors["dim"])

    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, sym, colors["orange"], bold=True)
    if name:
        _put(stdscr, r, 2 + len(sym) + 2, name[:width - len(sym) - 10],
             colors["header"], bold=True)
    r += 1
    _put(stdscr, r, 2, typ, type_color, bold=True); r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    lv(r, "Expiration:",    expiry);                        r += 1
    lv(r, "Strike:",        strike);                        r += 1
    lv(r, "Volume:",        vol);                           r += 1
    lv(r, "Open Interest:", oi);                            r += 1
    lv(r, "Vol/OI Ratio:",  voi);                           r += 1
    lv(r, "IV:",            iv);                            r += 1
    lv(r, "Bid:",           bid);                           r += 1
    lv(r, "Ask:",           ask);                           r += 1
    lv(r, "Last:",          last);                          r += 1
    lv(r, "Time:",          time_);                         r += 1

    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, "Press Enter to collapse", colors["dim"])
