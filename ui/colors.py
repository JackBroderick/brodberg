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
  6 = blue on blue     (water fill -- SHIP map)
  7 = black on orange  (land labels -- SHIP map)
  8 = white on blue    (water labels -- SHIP map)
  9 = green on blue    (ship markers on water -- SHIP map)

Orange selection strategy (best to worst):
  1. curses.init_color() custom RGB  -- exact Bloomberg orange (1000, 647, 0)
                                        requires can_change_color() + 256 colors
  2. xterm-256 color 214             -- close orange, requires COLORS >= 256
  3. xterm-16 color 11               -- bright yellow (actually yellow on 16-color
                                        terminals, not the olive-green of color 3)
                                        requires COLORS >= 16
  4. curses.COLOR_WHITE              -- clean neutral fallback; COLOR_YELLOW (index 3)
                                        renders as green on many 8-color terminals
                                        so white is always safer

To add a new color pair:
  1. Add a new curses.init_pair() call with the next available ID.
  2. Add it to the returned dict with a descriptive key.
"""

import curses


# 256-color palette index closest to Bloomberg orange (RGB 255,165,0)
_COLOR_214 = 214   # xterm-256: bright orange
_COLOR_11  = 11    # xterm-16:  bright yellow (renders yellow, not olive-green)


def _best_orange() -> int:
    """
    Return the best available color index for orange on this terminal.

    Tier 1 -- exact RGB (256-color terminals that allow color redefinition)
    Tier 2 -- xterm-256 palette index 214 (true orange)
    Tier 3 -- xterm-16 color 11 (bright yellow; actually renders as yellow,
              unlike COLOR_YELLOW / index 3 which is olive-green on many terminals)
    Tier 4 -- COLOR_WHITE; safe neutral that is never mistaken for green
    """
    # Tier 1: custom RGB
    if curses.can_change_color() and curses.COLORS >= 256:
        try:
            curses.init_color(10, 1000, 647, 0)   # Bloomberg orange, 0-1000 scale
            return 10
        except Exception:
            pass

    # Tier 2: 256-color palette
    if curses.COLORS >= 256:
        return _COLOR_214

    # Tier 3: 16-color -- bright yellow (index 11), not the green-prone index 3
    if curses.COLORS >= 16:
        return _COLOR_11

    # Tier 4: 8-color -- white is always white; COLOR_YELLOW renders green on
    # many terminals (ANSI color 3 is olive / dark yellow in most palettes)
    return curses.COLOR_WHITE


def init_colors() -> dict:
    """
    Initialize terminal colors and return a dict of named color pairs.
    Must be called after curses.initscr() / curses.wrapper().
    """
    curses.start_color()
    curses.use_default_colors()

    orange = _best_orange()

    # True black for text on orange backgrounds.
    # xterm-256 index 16 is unaffected by terminal theme remapping;
    # fall back to COLOR_BLACK on 8/16-color terminals.
    dark = 16 if curses.COLORS >= 256 else curses.COLOR_BLACK

    # Dim foreground: white on 256-color terminals; on 8-color terminals
    # use COLOR_WHITE which stays white regardless of terminal theme.
    dim_fg = curses.COLOR_WHITE

    curses.init_pair(1, orange,            curses.COLOR_BLACK)  # orange on black
    curses.init_pair(2, dark,              orange)              # black on orange (header)
    curses.init_pair(3, dim_fg,            curses.COLOR_BLACK)  # white on black (dim)
    curses.init_pair(4, curses.COLOR_GREEN, curses.COLOR_BLACK) # green on black (positive)
    curses.init_pair(5, curses.COLOR_RED,   curses.COLOR_BLACK) # red on black (negative)
    curses.init_pair(6, curses.COLOR_BLUE,  curses.COLOR_BLUE)  # blue on blue (water)
    curses.init_pair(7, dark,              orange)              # black on orange (land label)
    curses.init_pair(8, curses.COLOR_WHITE, curses.COLOR_BLUE)  # white on blue (water label)
    curses.init_pair(9, curses.COLOR_GREEN, curses.COLOR_BLUE)  # green on blue (ship)
    curses.init_pair(10, curses.COLOR_BLACK, curses.COLOR_WHITE) # black on white (highlight)

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
