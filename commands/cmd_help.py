"""
commands/cmd_help.py
--------------------
Implements the  HELP  command.

Reads docs/HelpMenu.txt, splits it into sections on  ## Header  lines,
and renders one section at a time with a navigable tab bar.

  fetch(parts)                  -> cache dict
  render(stdscr, cache, colors) -> None
  on_keypress(key, cache)       -> cache dict   ← → cycle sections  ↑ ↓ scroll
"""

import curses
import sys
import os


# ---------------------------------------------------------------------------
# PyInstaller-safe resource path
# ---------------------------------------------------------------------------

def _resource_path(relative: str) -> str:
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


HELP_FILE = os.path.join("docs", "HelpMenu.txt")


# ---------------------------------------------------------------------------
# Parsing — split file into sections on lines that start with  ##
# ---------------------------------------------------------------------------

def _parse_sections(text: str) -> list[dict]:
    """
    Return a list of {"name": str, "lines": [str, ...]} dicts.
    Section boundaries are lines whose stripped form starts with  ##.
    """
    sections = []
    current_name  = None
    current_lines = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("##"):
            if current_name is not None:
                sections.append({"name": current_name, "lines": current_lines})
            current_name  = stripped.lstrip("#").strip()
            current_lines = []
        else:
            if current_name is not None:
                current_lines.append(raw_line)

    if current_name is not None:
        sections.append({"name": current_name, "lines": current_lines})

    return sections


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    path = _resource_path(HELP_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return {"sections": [], "active": 0, "error": f"Help file not found: {path}"}

    sections = _parse_sections(text)
    if not sections:
        return {"sections": [], "active": 0, "error": "Help file contains no sections."}

    return {"sections": sections, "active": 0, "scroll": 0, "error": None}


# ---------------------------------------------------------------------------
# on_keypress — ← → cycle sections
# ---------------------------------------------------------------------------

def on_keypress(key: int, cache: dict) -> dict:
    sections = cache.get("sections", [])
    if not sections:
        return cache

    active = cache.get("active", 0)
    scroll = cache.get("scroll", 0)
    n      = len(sections)

    if key == curses.KEY_RIGHT:
        return {**cache, "active": (active + 1) % n, "scroll": 0}
    if key == curses.KEY_LEFT:
        return {**cache, "active": (active - 1) % n, "scroll": 0}
    if key == curses.KEY_DOWN:
        max_scroll = max(0, len(sections[active]["lines"]) - 1)
        return {**cache, "scroll": min(scroll + 1, max_scroll)}
    if key == curses.KEY_UP:
        return {**cache, "scroll": max(0, scroll - 1)}

    return cache


# ---------------------------------------------------------------------------
# render — called every frame
# ---------------------------------------------------------------------------

def _put(stdscr, row, col, text, color, bold=False):
    attr = color | (curses.A_BOLD if bold else 0)
    try:
        stdscr.attron(attr)
        stdscr.addstr(row, col, text)
        stdscr.attroff(attr)
    except Exception:
        pass


def render(stdscr, cache: dict, colors: dict) -> None:
    height, width = stdscr.getmaxyx()

    error = cache.get("error")
    if error:
        _put(stdscr, 4, 0, f"  {error}", colors["negative"])
        return

    sections = cache.get("sections", [])
    active   = cache.get("active", 0)

    if not sections:
        _put(stdscr, 4, 0, "  No help content available.", colors["dim"])
        return

    sep = f"  {'─' * (width - 4)}"

    # ── Tab bar ───────────────────────────────────────────────────────────
    r   = 4
    col = 2
    _put(stdscr, r, 0, sep, colors["dim"])
    r  += 1

    for i, sec in enumerate(sections):
        if i == active:
            label = f"[ {sec['name']} ]"
            _put(stdscr, r, col, label, colors["orange"], bold=True)
        else:
            label = f"  {sec['name']}  "
            _put(stdscr, r, col, label, colors["dim"])
        col += len(label)

    _put(stdscr, r + 1, 0, sep, colors["dim"])
    r += 2

    # ── Section content ───────────────────────────────────────────────────
    lines        = sections[active]["lines"]
    content_top  = r
    # Reserve 1 row at the bottom for the scroll indicator when needed
    available    = height - content_top - 2
    total        = len(lines)

    # Clamp scroll so we never scroll past the last page
    scroll = cache.get("scroll", 0)
    max_scroll = max(0, total - available)
    scroll = min(scroll, max_scroll)

    visible = lines[scroll:scroll + available]
    for line in visible:
        _put(stdscr, r, 0, line, colors["orange"])
        r += 1

    # ── Scroll indicator ─────────────────────────────────────────────────
    if total > available:
        end   = min(scroll + available, total)
        label = f"  ↑ ↓   {scroll + 1}–{end} / {total} lines"
        _put(stdscr, height - 2, 2, label, colors["dim"])
