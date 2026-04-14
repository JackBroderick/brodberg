"""
commands/cmd_chat.py
--------------------
Implements the CHAT command — real-time chat with general room and DMs.

  CHAT               — open the general chat room
  CHAT <username>    — open a DM with that user (general room also available)

Requires login.  Uses form_mode=True so main.py routes ALL keystrokes here.

Key bindings (in PANE mode):
  Any printable key  — type into the compose bar
  Backspace          — delete last character
  Enter              — send message
  Tab                — cycle between open rooms
  ↑ / ↓             — scroll message history
  ` (backtick)       — return to INPUT mode  (handled by main.py, not here)
"""

import curses
from datetime import datetime, timezone

import brodberg_session
import chat_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _room_label(room: str, me: str) -> str:
    if room == "general":
        return "#general"
    if room.startswith("dm:"):
        parts = room[3:].split(":")
        other = next((p for p in parts if p != me), parts[0])
        return f"DM: {other}"
    return room


def _dm_room(me: str, other: str) -> str:
    return "dm:" + ":".join(sorted([me.lower(), other.lower()]))


def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr)
        stdscr.addstr(row, col, text)
        stdscr.attroff(attr)
    except Exception:
        pass


def _fmt_ts(ts: str) -> str:
    """Convert an ISO UTC timestamp to local HH:MM.  Falls back gracefully."""
    if not ts:
        return "     "
    try:
        dt = datetime.fromisoformat(ts)          # parses "2026-04-13T20:45:37+00:00"
        return dt.astimezone().strftime("%H:%M")  # system local time
    except (ValueError, TypeError):
        return f"{ts[:5]:>5}"                     # old "HH:MM" rows — display as-is


# ---------------------------------------------------------------------------
# fetch — called once when the user presses Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    if not brodberg_session.is_logged_in():
        return {
            "error":    "Not logged in.  Use  USER login  first.",
            "form_mode": False,
            "rooms":    [],
            "active_room": 0,
            "compose":  "",
            "scroll":   0,
            "me":       None,
        }

    me     = brodberg_session.get_current_user()
    rooms  = ["general"]
    active = 0

    if len(parts) > 1:
        target = parts[1].lower()
        if target != me.lower():
            dm = _dm_room(me, target)
            rooms.append(dm)
            active = 1

    chat_data.connect(initial_rooms=list(rooms))

    return {
        "error":       None,
        "form_mode":   True,
        "me":          me,
        "rooms":       rooms,
        "active_room": active,
        "compose":     "",
        "scroll":      0,
    }


# ---------------------------------------------------------------------------
# on_keypress — ALL keys routed here when form_mode is True
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    if cache.get("error"):
        return cache

    rooms       = cache.get("rooms", ["general"])
    active_room = cache.get("active_room", 0)
    compose     = cache.get("compose", "")
    scroll      = cache.get("scroll", 0)
    me          = cache.get("me", "")

    # Tab — cycle rooms
    if key == 9:
        new_idx = (active_room + 1) % len(rooms)
        chat_data.join_room(rooms[new_idx])   # request history if not loaded
        return {**cache, "active_room": new_idx, "scroll": 0}

    # ↑ — scroll up (older messages)
    if key == curses.KEY_UP:
        total     = len(chat_data.get_messages(rooms[active_room]))
        max_scroll = max(0, total - 1)
        return {**cache, "scroll": min(scroll + 1, max_scroll)}

    # ↓ — scroll down (newer messages)
    if key == curses.KEY_DOWN:
        return {**cache, "scroll": max(0, scroll - 1)}

    # Backspace
    if key in (curses.KEY_BACKSPACE, 127, 8):
        return {**cache, "compose": compose[:-1]}

    # Enter — send
    if key in (curses.KEY_ENTER, 10, 13):
        text = compose.strip()
        if text:
            room = rooms[active_room]
            if text.startswith("/"):
                # Admin slash command
                cmd_parts = text[1:].split()
                action    = cmd_parts[0].lower() if cmd_parts else ""
                target    = cmd_parts[1].lower() if len(cmd_parts) > 1 else ""
                if action in ("mute", "unmute", "ban", "unban", "kick") and target:
                    chat_data.send({
                        "type":   "admin",
                        "action": action,
                        "target": target,
                        "room":   room,
                    })
                elif action == "del":
                    chat_data.send({
                        "type":        "admin",
                        "action":      "delete",
                        "room":        room,
                        "target_user": target or None,
                    })
            elif room == "general":
                chat_data.send({"type": "message", "room": "general", "text": text})
            else:
                # DM room: extract the other participant
                parts = room[3:].split(":")
                to    = next((p for p in parts if p != me.lower()), None)
                if to:
                    chat_data.send({"type": "dm", "to": to, "text": text})
        return {**cache, "compose": "", "scroll": 0}

    # Printable character
    if 32 <= key <= 126:
        if len(compose) < 400:
            return {**cache, "compose": compose + chr(key)}

    return cache


# ---------------------------------------------------------------------------
# render — called every frame
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    height, width = stdscr.getmaxyx()

    # ── Error state (not logged in) ───────────────────────────────────────
    error = cache.get("error")
    if error:
        sep = f"  {'─' * (width - 4)}"
        _put(stdscr, 4, 0, sep, colors["dim"])
        _put(stdscr, 5, 2, "CHAT", colors["orange"], bold=True)
        _put(stdscr, 6, 0, sep, colors["dim"])
        _put(stdscr, 8, 2, error, colors["negative"])
        return

    me          = cache.get("me", "")
    rooms       = cache.get("rooms", ["general"])
    active_room = cache.get("active_room", 0)
    compose     = cache.get("compose", "")
    scroll      = cache.get("scroll", 0)
    status      = chat_data.get_status()
    room        = rooms[active_room] if rooms else "general"

    sep = f"  {'─' * (width - 4)}"

    # ── Tab bar ───────────────────────────────────────────────────────────
    r   = 4
    col = 2
    _put(stdscr, r, 0, sep, colors["dim"])
    r += 1

    for i, rm in enumerate(rooms):
        label = _room_label(rm, me)
        if i == active_room:
            tag = f"[ {label} ]"
            _put(stdscr, r, col, tag, colors["orange"], bold=True)
        else:
            tag = f"  {label}  "
            _put(stdscr, r, col, tag, colors["dim"])
        col += len(tag)

    # Status badge (top-right)
    if status == "live":
        badge       = "[LIVE]"
        badge_color = colors["positive"]
    elif status == "connecting":
        badge       = "[CONNECTING]"
        badge_color = colors["dim"]
    else:
        badge       = f"[{status.upper()[:12]}]"
        badge_color = colors["negative"]
    _put(stdscr, r, width - len(badge) - 2, badge, badge_color, bold=(status == "live"))

    r += 1
    _put(stdscr, r, 0, sep, colors["dim"])
    r += 1   # r is now the first message row

    # ── Layout geometry ───────────────────────────────────────────────────
    # Bottom rows: separator + compose + hint + separator = 4 rows
    compose_sep_row = height - 5
    compose_row     = height - 4
    hint_row        = height - 3
    bottom_sep_row  = height - 2

    msg_area_top    = r                          # first message row
    msg_area_rows   = compose_sep_row - msg_area_top   # number of rows for messages

    if msg_area_rows < 1:
        return   # terminal too small

    # ── Messages ──────────────────────────────────────────────────────────
    msgs   = chat_data.get_messages(room)
    total  = len(msgs)

    # Clamp scroll
    max_scroll = max(0, total - msg_area_rows)
    scroll     = min(scroll, max_scroll)

    end_idx   = max(0, total - scroll)
    start_idx = max(0, end_idx - msg_area_rows)
    visible   = msgs[start_idx:end_idx]

    name_w   = 14   # wide enough for "jackbroderick" (13) + 1
    # Layout: col 2 = timestamp (5), col 9 = name (name_w) + ":", col 9+name_w+2 = text
    text_col = 9 + name_w + 2   # +1 colon +1 space

    for i, msg in enumerate(visible):
        row    = msg_area_top + i
        sender = msg.get("from", "")
        text   = msg.get("text", "")
        ts     = msg.get("ts", "")
        is_me  = (sender == me)
        is_sys = (sender == "system")

        if is_sys:
            indent = " " * text_col
            _put(stdscr, row, 0, (indent + text)[:width - 1], colors["dim"])
            continue

        is_admin_sender = msg.get("admin", False)
        ts_str = _fmt_ts(ts)

        # Admin gets a star marker; truncate name to keep total width = name_w
        if is_admin_sender:
            name_str = sender[:(name_w - 2)].rjust(name_w - 2) + " ★"
        else:
            name_str = sender[:name_w].rjust(name_w)

        _put(stdscr, row, 2, ts_str, colors["dim"])
        name_color = colors["orange"] if (is_me or is_admin_sender) else colors["dim"]
        _put(stdscr, row, 9, name_str + ":", name_color, bold=(is_me or is_admin_sender))

        max_text = max(0, width - text_col - 1)
        _put(stdscr, row, text_col, text[:max_text],
             colors["orange"] if is_me else colors["dim"])

    # ── Scroll indicator ──────────────────────────────────────────────────
    if total > msg_area_rows and scroll > 0:
        ind = f"  ↑ {scroll} more above"
        _put(stdscr, msg_area_top, width - len(ind) - 2, ind, colors["dim"])

    # ── Compose area ──────────────────────────────────────────────────────
    _put(stdscr, compose_sep_row, 0, sep, colors["dim"])

    # Show the tail of the compose string so the cursor is always visible
    compose_inner = width - 8    # "  >  " (5) + right margin (3)
    display       = compose[-compose_inner:] if len(compose) > compose_inner else compose
    cursor        = "▌"
    compose_line  = f"  >  {display}{cursor}"
    _put(stdscr, compose_row, 0, compose_line[:width - 1], colors["orange"])

    hint = "  Tab room   ↑↓ scroll   Enter send   /kick /mute /ban /del <user>   ` exit"
    _put(stdscr, hint_row, 0, hint[:width - 1], colors["dim"])

    _put(stdscr, bottom_sep_row, 0, sep, colors["dim"])
