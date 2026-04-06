"""
commands/cmd_changelog.py
-------------------------
Implements the  CL  command (Change Log).

Reads docs/ChangeLog.txt and displays the most recent N version blocks.
Uses resource_path() so the file is found both when running as a
raw script and when bundled into a PyInstaller .exe.

A "version block" begins with any line that starts with "V" followed
by a digit (e.g. "V1.3.0b", "V0.5.1").

To change how many versions are shown, adjust VERSIONS_TO_SHOW.

  fetch(parts)                  -> cache dict
  render(stdscr, cache, colors) -> None
"""

import sys
import os

VERSIONS_TO_SHOW = 3   # number of most-recent version blocks to display


# ---------------------------------------------------------------------------
# PyInstaller-safe resource path
# ---------------------------------------------------------------------------
# This file lives at  commands/cmd_changelog.py  — one level below the
# project root.  We walk up one directory so the base is always the root.
# ---------------------------------------------------------------------------

def _resource_path(relative: str) -> str:
    """
    Resolve a path relative to the PROJECT ROOT.
    Works both as a raw script and as a PyInstaller --onefile .exe.
    """
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


CHANGELOG_FILE = os.path.join("docs", "ChangeLog.txt")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_recent_versions(raw: str, n: int) -> str:
    """
    Parse `raw` into version blocks and return the first `n` as a string.
    A block starts on any line beginning with "V<digit>" and ends just
    before the next such line (or end of file).
    """
    lines = raw.splitlines()

    block_starts = [
        i for i, line in enumerate(lines)
        if len(line) >= 2 and line[0] == "V" and line[1].isdigit()
    ]

    if not block_starts:
        return raw

    end_line = block_starts[n] if n < len(block_starts) else len(lines)
    selected_lines = lines[block_starts[0]: end_line]
    return "\n".join(selected_lines)


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    """Load and trim the changelog to the most recent version blocks."""
    path = _resource_path(CHANGELOG_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        content = _extract_recent_versions(raw, VERSIONS_TO_SHOW)
        return {"content": content, "error": None}
    except FileNotFoundError:
        return {"content": None, "error": f"Changelog file '{path}' not found."}


# ---------------------------------------------------------------------------
# render — called every frame
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    error = cache.get("error")
    if error:
        try:
            stdscr.attron(colors["negative"])
            stdscr.addstr(4, 0, f"  {error}")
            stdscr.attroff(colors["negative"])
        except Exception:
            pass
        return

    content = cache.get("content", "")
    try:
        stdscr.attron(colors["orange"])
        stdscr.addstr(4, 0, content)
        stdscr.attroff(colors["orange"])
    except Exception:
        pass