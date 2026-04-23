"""
commands/cmd_watch.py
---------------------
WATCH — personal ticker watchlist, stored server-side per account.

Requires login. Uses form_mode=True so main.py routes ALL keystrokes here.

Key bindings:
  Printable keys   — type ticker in add field (auto-uppercased, max 10 chars)
  Backspace        — delete last char in add field
  Enter            — validate and add the typed ticker
  ↓ (from add)     — move focus down to the list
  ↑ (at top of list) — move focus back to add field
  ↑ / ↓            — navigate list items
  D                — remove selected ticker from watchlist
"""

import curses
import requests

import broderick_session
import watchlist_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr)
        stdscr.addstr(row, col, str(text))
        stdscr.attroff(attr)
    except Exception:
        pass


def _http_detail(e: requests.HTTPError) -> str:
    try:
        return e.response.json().get("detail", str(e))
    except Exception:
        return str(e)


# ---------------------------------------------------------------------------
# fetch — called once when the user types WATCH
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    base = {
        "error":         None,
        "form_mode":     True,
        "watchlist":     [],
        "selected":      0,
        "list_offset":   0,
        "input":         "",
        "input_focused": True,
        "status":        "",
        "me":            None,
    }

    if not broderick_session.is_logged_in():
        base["error"]     = "Not logged in — use  USER LOGIN  first."
        base["form_mode"] = False
        return base

    base["me"] = broderick_session.get_current_user()

    try:
        base["watchlist"] = watchlist_data.get_watchlist()
    except Exception as e:
        base["status"] = f"  Error loading watchlist: {e}"

    return base


# ---------------------------------------------------------------------------
# on_keypress — ALL keys routed here when form_mode is True
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    if cache.get("error"):
        return cache

    input_focused = cache.get("input_focused", True)
    text          = cache.get("input", "")
    watchlist     = cache.get("watchlist", [])
    selected      = cache.get("selected", 0)

    if input_focused:
        if 32 <= key <= 126:
            if len(text) < 10:
                cache["input"]  = text + chr(key).upper()
                cache["status"] = ""

        elif key in (curses.KEY_BACKSPACE, 127, 8):
            cache["input"] = text[:-1]

        elif key in (curses.KEY_ENTER, 10, 13):
            ticker = text.strip().upper()
            if ticker:
                try:
                    watchlist_data.add_ticker(ticker)
                    cache["watchlist"]    = watchlist_data.get_watchlist()
                    cache["status"]       = f"  {ticker} added."
                    cache["selected"]     = len(cache["watchlist"]) - 1
                    cache["list_offset"]  = max(0, cache["selected"] - 5)
                except requests.HTTPError as e:
                    cache["status"] = f"  {_http_detail(e)}"
                except Exception as e:
                    cache["status"] = f"  Error: {e}"
                cache["input"] = ""

        elif key == curses.KEY_DOWN and watchlist:
            cache["input_focused"] = False
            cache["selected"]      = 0
            cache["list_offset"]   = 0

    else:
        n = len(watchlist)

        if key == curses.KEY_UP:
            if selected > 0:
                cache["selected"] = selected - 1
            else:
                cache["input_focused"] = True

        elif key == curses.KEY_DOWN:
            if selected < n - 1:
                cache["selected"] = selected + 1

        elif key in (ord("d"), ord("D")):
            if watchlist and 0 <= selected < n:
                ticker = watchlist[selected]["ticker"]
                try:
                    watchlist_data.remove_ticker(ticker)
                    cache["watchlist"] = watchlist_data.get_watchlist()
                    new_n              = len(cache["watchlist"])
                    cache["selected"]  = min(selected, max(0, new_n - 1))
                    if new_n == 0:
                        cache["input_focused"] = True
                    cache["status"] = f"  {ticker} removed."
                except Exception as e:
                    cache["status"] = f"  Error: {e}"

    return cache


# ---------------------------------------------------------------------------
# render — called every frame, must never hit the API
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    height, width = stdscr.getmaxyx()
    sep = "  " + "─" * max(0, width - 4)

    r = 4

    # ── Error state (not logged in) ───────────────────────────────────────────
    if cache.get("error"):
        _put(stdscr, r,     0, sep, colors["dim"])
        _put(stdscr, r + 1, 2, "WATCHLIST", colors["orange"], bold=True)
        _put(stdscr, r + 2, 0, sep, colors["dim"])
        _put(stdscr, r + 4, 2, cache["error"], colors["negative"])
        return

    watchlist     = cache.get("watchlist", [])
    selected      = cache.get("selected", 0)
    input_focused = cache.get("input_focused", True)
    input_text    = cache.get("input", "")
    status        = cache.get("status", "")

    # ── Header ────────────────────────────────────────────────────────────────
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, "WATCHLIST", colors["orange"], bold=True)
    count_label = f"{len(watchlist)} / 25"
    _put(stdscr, r, max(0, width - len(count_label) - 2), count_label, colors["dim"])
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    r += 1  # breathing room

    # ── Add field ─────────────────────────────────────────────────────────────
    field_w   = min(12, max(4, width - 14))
    disp_text = input_text[-field_w:] if len(input_text) > field_w else input_text
    cursor    = "_" if input_focused else " "
    add_line  = f"  Add: [{disp_text.ljust(field_w)}{cursor}]"
    _put(stdscr, r, 0, add_line,
         colors["orange"] if input_focused else colors["dim"])
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    list_start_row = r
    max_list_rows  = max(0, height - list_start_row - 2)

    # ── Empty watchlist ───────────────────────────────────────────────────────
    if not watchlist:
        _put(stdscr, r, 2, "  No tickers yet — type one above and press Enter.",
             colors["dim"])
        if status:
            s_color = colors["negative"] if "Error" in status else colors["dim"]
            _put(stdscr, height - 2, 0, status[:width - 1], s_color)
        _put(stdscr, height - 1, 0, "  Enter = add ticker", colors["dim"])
        return

    # ── Scroll offset — keep selected row in view ─────────────────────────────
    list_offset = cache.get("list_offset", 0)
    if selected < list_offset:
        list_offset = selected
    elif max_list_rows > 0 and selected >= list_offset + max_list_rows:
        list_offset = selected - max_list_rows + 1
    cache["list_offset"] = list_offset

    # ── Ticker rows ───────────────────────────────────────────────────────────
    visible = watchlist[list_offset:list_offset + max_list_rows]
    for i, item in enumerate(visible):
        if r >= height - 2:
            break
        abs_idx = list_offset + i
        is_sel  = (not input_focused and abs_idx == selected)
        ticker  = item.get("ticker", "")
        quote   = item.get("quote", {})
        c       = quote.get("c", 0.0)
        dp      = quote.get("dp", 0.0)

        bullet    = "●" if is_sel else " "
        price_str = f"${c:>10,.2f}" if c else "       N/A"
        pct_str   = f"{dp:>+7.2f}%" if dp else "        "
        arrow     = "▲" if dp > 0 else ("▼" if dp < 0 else "─")
        line      = f" {bullet} {ticker:<7}  {price_str}  {pct_str}  {arrow}"

        if is_sel:
            color = colors["orange"] | curses.A_BOLD
        elif dp > 0:
            color = colors["positive"]
        elif dp < 0:
            color = colors["negative"]
        else:
            color = colors["orange"]

        _put(stdscr, r, 0, line[:width - 1], color)
        r += 1

    # ── Status ────────────────────────────────────────────────────────────────
    if status:
        s_color = colors["negative"] if "Error" in status else colors["dim"]
        _put(stdscr, height - 2, 0, status[:width - 1], s_color)

    # ── Hint + scroll indicator ───────────────────────────────────────────────
    scroll_info = ""
    if len(watchlist) > max_list_rows > 0:
        shown_end   = list_offset + min(max_list_rows, len(watchlist) - list_offset)
        scroll_info = f"   [{list_offset + 1}-{shown_end}/{len(watchlist)}]"
    hint = f"  ↑↓ navigate  ·  D = remove{scroll_info}"
    _put(stdscr, height - 1, 0, hint[:width - 1], colors["dim"])
