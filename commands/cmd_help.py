"""
commands/cmd_help.py
--------------------
HELP — scrollable command directory.

Displays all commands grouped by category.  Selecting a command with Enter
either opens it directly (no-arg commands) or shows its usage syntax.

Key bindings:
  ↑ / ↓     navigate the list
  ← / →     cycle category filter
  Enter      open command (or show usage if arguments are required)
"""

import curses


# ---------------------------------------------------------------------------
# Command catalog
# (category, display, description, launch_string_or_None)
#
# launch = string passed to process_command when Enter is pressed.
# None   = command requires arguments; pressing Enter shows its usage instead.
# ---------------------------------------------------------------------------

_CATEGORIES = ["ALL", "EQUITY", "MACRO", "NEWS", "ACCOUNT", "GENERAL"]

_COMMANDS = [
    # ── Equity ────────────────────────────────────────────────────────────────
    ("EQUITY",  "Q <TICKER>",               "Live quote",                           None),
    ("EQUITY",  "GIP <TICKER> [TF]",        "Price chart  (1W  1M  3M  YTD  1Y)",  None),
    ("EQUITY",  "DES <TICKER>",             "Company description & profile",        None),
    ("EQUITY",  "FA <TICKER> [IS|BS|CF]",   "Financial statements",                 None),
    ("EQUITY",  "PEERS <TICKER>",           "Peer / competitor companies",          None),
    ("EQUITY",  "EXEC <TICKER>",            "Executive team & compensation",        None),
    ("EQUITY",  "DIV <TICKER>",             "Dividend history",                     None),
    ("EQUITY",  "OWN <TICKER>",             "Insider & ownership transactions",     None),
    ("EQUITY",  "SENT <TICKER>",            "News sentiment & buzz metrics",        None),
    ("EQUITY",  "OMON <TICKER>",            "Options chain monitor",                None),
    ("EQUITY",  "REV <TICKER>",             "Revenue breakdown by segment",          None),

    # ── Macro ─────────────────────────────────────────────────────────────────
    ("MACRO",   "RATES",                    "U.S. Treasury yield curve",            "RATES"),
    ("MACRO",   "COMD",                     "Commodities dashboard",                "COMD"),
    ("MACRO",   "FX [G10|EM]",              "FX major pairs vs USD",                "FX"),
    ("MACRO",   "UO",                       "Unusual options activity",             "UO"),
    ("MACRO",   "EARN",                     "Earnings calendar",                    "EARN"),
    ("MACRO",   "IPO [from] [to]",          "IPO calendar",                         "IPO"),
    ("MACRO",   "SHIP HORMUZ",              "Live AIS vessel tracking",             "SHIP HORMUZ"),

    # ── News ──────────────────────────────────────────────────────────────────
    ("NEWS",    "N",                        "Market news feed",                     "N"),
    ("NEWS",    "N <TICKER>",               "Company-specific news",                None),

    # ── Account ───────────────────────────────────────────────────────────────
    ("ACCOUNT", "WATCH",                    "Personal ticker watchlist",            "WATCH"),
    ("ACCOUNT", "CHAT",                     "General chat rooms",                   "CHAT"),
    ("ACCOUNT", "USER",                     "View your profile",                    "USER"),
    ("ACCOUNT", "USER LOGIN",               "Sign in",                              "USER LOGIN"),
    ("ACCOUNT", "USER REGISTER",            "Create an account",                    "USER REGISTER"),
    ("ACCOUNT", "USER LOGOUT",              "Sign out",                             "USER LOGOUT"),
    ("ACCOUNT", "USER EDIT",                "Edit bio & location",                  "USER EDIT"),

    # ── General ───────────────────────────────────────────────────────────────
    ("GENERAL", "CL",                       "Version changelog",                    "CL"),
    ("GENERAL", "CLEAR",                    "Clear this pane",                      None),
    ("GENERAL", "EXIT",                     "Close the terminal",                   None),
]

_CMD_COL_W = 26   # fixed width of the command-text column


def _base_command(display: str) -> str:
    """Extract the runnable base from a display string like 'Q <TICKER> [TF]' → 'Q '."""
    base = display.split("<")[0].split("[")[0].strip()
    return base + " "


def _filtered(cat: str) -> list:
    if cat == "ALL":
        return list(_COMMANDS)
    return [c for c in _COMMANDS if c[0] == cat]


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    return {
        "cat_idx":   0,
        "selected":  0,
        "scroll":    0,
        "usage_hint": "",
    }


# ---------------------------------------------------------------------------
# on_keypress
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    cat_idx  = cache.get("cat_idx", 0)
    selected = cache.get("selected", 0)
    scroll   = cache.get("scroll", 0)

    rows = _filtered(_CATEGORIES[cat_idx])
    n    = len(rows)

    if key == curses.KEY_LEFT:
        new_idx = (cat_idx - 1) % len(_CATEGORIES)
        return {**cache, "cat_idx": new_idx, "selected": 0, "scroll": 0, "usage_hint": ""}

    if key == curses.KEY_RIGHT:
        new_idx = (cat_idx + 1) % len(_CATEGORIES)
        return {**cache, "cat_idx": new_idx, "selected": 0, "scroll": 0, "usage_hint": ""}

    if key == curses.KEY_UP:
        return {**cache, "selected": max(0, selected - 1), "usage_hint": ""}

    if key == curses.KEY_DOWN:
        return {**cache, "selected": min(n - 1, selected + 1), "usage_hint": ""}

    if key in (curses.KEY_ENTER, 10, 13):
        if 0 <= selected < n:
            _cat, display, _desc, launch = rows[selected]
            if launch:
                return {**cache, "launch_command": launch, "usage_hint": ""}
            else:
                prefill = _base_command(display)
                return {**cache,
                        "prefill_input": prefill,
                        "usage_hint":    f"  '{prefill.strip()}' ready — type ticker + Enter  ·  ` to cancel"}

    return cache


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr)
        stdscr.addstr(row, col, str(text))
        stdscr.attroff(attr)
    except Exception:
        pass


def render(stdscr, cache: dict, colors: dict) -> None:
    height, width = stdscr.getmaxyx()
    sep = "  " + "─" * max(0, width - 4)

    cat_idx     = cache.get("cat_idx", 0)
    selected    = cache.get("selected", 0)
    scroll      = cache.get("scroll", 0)
    usage_hint  = cache.get("usage_hint", "")
    active_cat  = _CATEGORIES[cat_idx]
    rows        = _filtered(active_cat)
    n           = len(rows)

    r = 4

    # ── Header ────────────────────────────────────────────────────────────────
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1
    _put(stdscr, r, 2, "HELP", colors["orange"], bold=True)
    _put(stdscr, r, 8, "·  Command Directory", colors["dim"])
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    # ── Category filter bar ───────────────────────────────────────────────────
    col = 2
    for cat in _CATEGORIES:
        if cat == active_cat:
            label = f"[{cat}]"
            _put(stdscr, r, col, label, colors["orange"], bold=True)
        else:
            label = f" {cat} "
            _put(stdscr, r, col, label, colors["dim"])
        col += len(label) + 1
        if col >= width - 2:
            break
    r += 1
    _put(stdscr, r, 0, sep, colors["dim"]); r += 1

    list_top      = r
    max_visible   = max(0, height - list_top - 2)

    if not rows:
        _put(stdscr, r, 2, "  No commands in this category.", colors["dim"])
        return

    # ── Scroll: keep selected in view ─────────────────────────────────────────
    if selected < scroll:
        scroll = selected
    elif max_visible > 0 and selected >= scroll + max_visible:
        scroll = selected - max_visible + 1
    cache["scroll"] = scroll

    # ── Command rows ──────────────────────────────────────────────────────────
    visible = rows[scroll:scroll + max_visible]
    for i, (cat, display, desc, launch) in enumerate(visible):
        if r >= height - 2:
            break
        abs_i  = scroll + i
        is_sel = (abs_i == selected)
        bullet = "●" if is_sel else " "

        display_col = f" {bullet} {display:<{_CMD_COL_W}}"
        line        = f"{display_col}  {desc}"

        if is_sel:
            color = colors["orange"] | curses.A_BOLD
        elif launch:
            color = colors["orange"]
        else:
            color = colors["dim"]

        _put(stdscr, r, 0, line[:width - 1], color)
        r += 1

    # ── Usage hint (shown when Enter pressed on an arg-required command) ───────
    if usage_hint:
        _put(stdscr, height - 2, 0, usage_hint[:width - 1], colors["orange"])

    # ── Scroll indicator + nav hint ───────────────────────────────────────────
    scroll_info = ""
    if n > max_visible > 0:
        end         = scroll + min(max_visible, n - scroll)
        scroll_info = f"   [{scroll + 1}-{end}/{n}]"
    hint = f"  ↑↓ navigate  ·  ← → category  ·  Enter = open{scroll_info}"
    _put(stdscr, height - 1, 0, hint[:width - 1], colors["dim"])
