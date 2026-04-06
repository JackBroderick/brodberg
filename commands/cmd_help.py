"""
commands/cmd_help.py
--------------------
Implements the  HELP  command.

Reads docs/HelpMenu.txt and displays it starting at row 4.
Uses resource_path() so the file is found both when running as a
raw script and when bundled into a PyInstaller .exe.

  fetch(parts)                  -> cache dict
  render(stdscr, cache, colors) -> None
"""

import sys
import os


# ---------------------------------------------------------------------------
# PyInstaller-safe resource path
# ---------------------------------------------------------------------------
# This file lives at  commands/cmd_help.py  — one level below the project
# root.  We walk up one directory (os.path.dirname twice) so that the base
# is always the project root, whether running as a script or a .exe.
# ---------------------------------------------------------------------------

def _resource_path(relative: str) -> str:
    """
    Resolve a path relative to the PROJECT ROOT.
    Works both as a raw script and as a PyInstaller --onefile .exe.
    """
    if getattr(sys, "frozen", False):
        # Running inside a PyInstaller bundle — _MEIPASS is the root
        base = sys._MEIPASS
    else:
        # Running as a script — go up one level from commands/ to project root
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


HELP_FILE = os.path.join("docs", "HelpMenu.txt")


# ---------------------------------------------------------------------------
# fetch — called once on Enter
# ---------------------------------------------------------------------------

def fetch(parts: list) -> dict:
    """Load the help file contents into the cache."""
    path = _resource_path(HELP_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            return {"content": f.read(), "error": None}
    except FileNotFoundError:
        return {"content": None, "error": f"Help file '{path}' not found."}


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