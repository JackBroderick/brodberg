"""
ui/colors.py
------------
Defines and initializes all curses color pairs used across the terminal.

Color pair IDs:
  1 = orange on black  (main body text)
  2 = black on orange  (header / footer bar)
  3 = white on black   (dim labels, separators)
  4 = green on black   (positive price change)
  5 = red on black     (negative price change)
  6 = blue on blue     (water fill — SHIP map)
  7 = black on orange  (land labels — SHIP map)
  8 = white on blue    (water labels — SHIP map)
  9 = green on blue    (ship markers on water — SHIP map)

Orange selection strategy (best → fallback):
  1. curses.init_color()  — custom RGB, requires can_change_color() + 256 colors
  2. 256-color palette    — color 214 is a close orange, requires colors >= 256
  3. curses.COLOR_YELLOW  — universal fallback, available on all terminals

To add a new color pair:
  1. Add a new curses.init_pair() call with the next available ID.
  2. Add it to the returned dict with a descriptive key.
"""

import curses


# 256-color palette index closest to Bloomberg orange (RGB 255,165,0)
_COLOR_214 = 214   # xterm-256 index: bright orange


def _best_orange() -> int:
    """
    Return the best available orange color index for this terminal.

    Priority:
      1. Custom RGB via init_color (slot 10) — exact Bloomberg orange
      2. xterm-256 color 214              — close orange, no custom slot needed
      3. COLOR_YELLOW                     — always available, warm enough
    """
    # Tier 1 — full custom color
    if curses.can_change_color() and curses.COLORS >= 256:
        try:
            curses.init_color(10, 1000, 647, 0)   # Bloomberg orange RGB→0-1000
            return 10
        except Exception:
            pass

    # Tier 2 — 256-color palette has a good orange at index 214
    if curses.COLORS >= 256:
        return _COLOR_214

    # Tier 3 — 8-color fallback
    return curses.COLOR_YELLOW


def init_colors() -> dict:
    """
    Initialize terminal colors and return a dict of named color pairs.
    Must be called after curses.initscr() / curses.wrapper().
    """
    curses.start_color()
    curses.use_default_colors()

    orange = _best_orange()

    # Use xterm-256 index 16 (true black, not subject to terminal theme remapping)
    # as the foreground for orange-highlighted text so it renders as actual black.
    dark = 16 if curses.COLORS >= 256 else curses.COLOR_BLACK

    curses.init_pair(1, orange,            curses.COLOR_BLACK)  # orange on black
    curses.init_pair(2, dark,              orange)              # black on orange
    curses.init_pair(3, curses.COLOR_WHITE, curses.COLOR_BLACK) # white on black
    curses.init_pair(4, curses.COLOR_GREEN, curses.COLOR_BLACK) # green on black
    curses.init_pair(5, curses.COLOR_RED,   curses.COLOR_BLACK) # red on black
    curses.init_pair(6, curses.COLOR_BLUE,  curses.COLOR_BLUE)  # blue on blue (water)
    curses.init_pair(7, dark,              orange)              # black on orange (land label)
    curses.init_pair(8, curses.COLOR_WHITE, curses.COLOR_BLUE)  # white on blue (water label)
    curses.init_pair(9,  curses.COLOR_GREEN, curses.COLOR_BLUE)  # green on blue (ship on water)
    curses.init_pair(10, curses.COLOR_BLACK, curses.COLOR_WHITE) # black on white (selected row)

    return {
        "orange":      curses.color_pair(1),
        "header":      curses.color_pair(2),
        "dim":         curses.color_pair(3),
        "positive":    curses.color_pair(4),
        "negative":    curses.color_pair(5),
        "water":       curses.color_pair(6),
        "land_label":  curses.color_pair(7),
        "water_label": curses.color_pair(8),
        "ship":        curses.color_pair(9),
        "highlight":   curses.color_pair(10),
    }