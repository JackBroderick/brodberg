"""
ui/chrome.py
------------
Persistent terminal UI elements that appear on every screen.

  draw_header()         — top bar with terminal name + clock       (row 0)
  draw_footer()         — bottom command bar                       (row height-1)
  draw_default_screen() — splash shown when no command is active
  draw_panes()          — renders up to 3 vertical command panes
                          between the chrome rows and the footer
  draw_zoom_tabs()      — tab bar shown in zoom mode               (row height-2)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ZOOM MODE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  When zoomed=True is passed to draw_panes(), only the focused pane is
  rendered, expanded to fill the full terminal width.  The other panes
  are collapsed into a tab bar drawn on row (height-2) by
  draw_zoom_tabs().  Pressing Z in pane mode toggles the flag.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import curses
import datetime


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def draw_header(stdscr, width: int, now: datetime.datetime, colors: dict) -> None:
    """Draw the full-width header bar on row 0."""
    formatted_time = now.strftime("%A, %d %B %Y, %I:%M:%S %p")
    content   = f"BRODBERG TERMINAL Beta V 4.0 |  {formatted_time}"
    full_text = content.center(width - 1)
    stdscr.attron(colors["header"])
    stdscr.addstr(0, 0, full_text[: width - 1])
    stdscr.attroff(colors["header"])


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

def draw_footer(stdscr, height: int, width: int, command: str,
                colors: dict, input_focused: bool = True) -> None:
    """
    Draw the full-width footer / command bar on the last row.

    If the prompt + command text is wider than the terminal, the command
    is scrolled left so the cursor (end of input) is always visible.
    The prompt " Command > " is always shown; only the command portion
    is clipped on the left when it overflows.

    Shows a bright prompt when input is focused, dim when in pane mode.
    """
    prompt     = " Command > "
    prompt_len = len(prompt)
    cmd_area   = width - prompt_len - 1   # columns available for the command

    if cmd_area < 1:
        return

    # Always show the tail of the command so the cursor stays visible
    visible_cmd = command[-cmd_area:] if len(command) > cmd_area else command
    bar = f"{prompt}{visible_cmd}"

    if input_focused:
        stdscr.attron(colors["header"])
    else:
        stdscr.attron(colors["dim"])

    stdscr.addstr(height - 1, 0, bar[: width - 1])

    if input_focused:
        stdscr.attroff(colors["header"])
    else:
        stdscr.attroff(colors["dim"])


# ---------------------------------------------------------------------------
# Default splash
# ---------------------------------------------------------------------------

def draw_default_screen(stdscr, colors: dict, col: int = 0) -> None:
    """Splash text shown when a pane has no active command."""
    try:
        stdscr.attron(colors["dim"])
        stdscr.addstr(5, col + 2, "  Enter a command or type HELP")
        stdscr.addstr(6, col + 2, "  to get started.")
        stdscr.attroff(colors["dim"])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Multi-pane layout
# ---------------------------------------------------------------------------

# Rows reserved at the top for header chrome (header + benchmark + news + gap)
CHROME_ROWS = 4


def draw_panes(stdscr, panes: list, focused_idx: int,
               colors: dict, dispatch_render_fn,
               zoomed: bool = False) -> None:
    """
    Divide the terminal horizontally into len(panes) equal vertical panes
    and render each one.

    When zoomed=True the focused pane expands to fill the full width.
    The tab bar (draw_zoom_tabs) is drawn separately by main.py.

    Parameters
    ----------
    stdscr             — curses window
    panes              — list of dicts: {"activecommand": str, "cache": dict}
    focused_idx        — index of the currently focused pane (gets bright border)
    colors             — color dict from init_colors()
    dispatch_render_fn — registry.dispatch_render
    zoomed             — if True, only render the focused pane at full width
    """
    height, width = stdscr.getmaxyx()
    n = len(panes)
    if n == 0:
        return

    pane_top    = CHROME_ROWS
    pane_bottom = (height - 2) if zoomed else (height - 1)
    pane_height = pane_bottom - pane_top

    if pane_height < 3:
        return

    if zoomed:
        # ── ZOOM MODE: single full-width pane ─────────────────────────────
        pane  = panes[focused_idx]
        pw    = width
        label = pane["activecommand"] or "EMPTY"

        try:
            stdscr.attron(colors["orange"])
            title    = f"[ {label}  + ZOOM ]"
            top_line = "┌" + title.center(pw - 2, "─") + "┐"
            stdscr.addstr(pane_top, 0, top_line[: pw])

            for r in range(pane_top + 1, pane_bottom - 1):
                stdscr.addstr(r, 0,      "│")
                stdscr.addstr(r, pw - 1, "│")

            bot_line = "└" + "─" * (pw - 2) + "┘"
            stdscr.addstr(pane_bottom - 1, 0, bot_line[: pw])
            stdscr.attroff(colors["orange"])
        except Exception:
            pass

        inner_w   = pw - 2
        inner_h   = pane_height - 2
        inner_top = pane_top + 1
        inner_col = 1

        if inner_w > 4 and inner_h > 2:
            try:
                sub = stdscr.derwin(inner_h, inner_w, inner_top, inner_col)
                sub.erase()
                if pane["activecommand"]:
                    dispatch_render_fn(sub, pane["activecommand"],
                                       pane["cache"], colors)
                else:
                    draw_default_screen(sub, colors, col=0)
            except Exception:
                pass

    else:
        # ── NORMAL MODE: equal-width side-by-side panes ───────────────────
        pane_widths = []
        base_w    = width // n
        remainder = width - base_w * n
        for i in range(n):
            pane_widths.append(base_w + (1 if i < remainder else 0))

        col_start = 0
        for i, pane in enumerate(panes):
            pw           = pane_widths[i]
            is_focused   = (i == focused_idx)
            border_color = colors["orange"] if is_focused else colors["dim"]
            label        = pane["activecommand"] or "EMPTY"

            try:
                stdscr.attron(border_color)
                title    = f"[ {label} ]"
                top_line = "┌" + title.center(pw - 2, "─") + "┐"
                stdscr.addstr(pane_top, col_start, top_line[: pw])

                for r in range(pane_top + 1, pane_bottom - 1):
                    stdscr.addstr(r, col_start,          "│")
                    stdscr.addstr(r, col_start + pw - 1, "│")

                bot_line = "└" + "─" * (pw - 2) + "┘"
                stdscr.addstr(pane_bottom - 1, col_start, bot_line[: pw])
                stdscr.attroff(border_color)
            except Exception:
                pass

            inner_w   = pw - 2
            inner_h   = pane_height - 2
            inner_top = pane_top + 1
            inner_col = col_start + 1

            if inner_w > 4 and inner_h > 2:
                try:
                    sub = stdscr.derwin(inner_h, inner_w, inner_top, inner_col)
                    sub.erase()
                    if pane["activecommand"]:
                        dispatch_render_fn(sub, pane["activecommand"],
                                           pane["cache"], colors)
                    else:
                        draw_default_screen(sub, colors, col=0)
                except Exception:
                    pass

            col_start += pw


# ---------------------------------------------------------------------------
# Zoom tab bar  (row height-2, only drawn in zoom mode)
# ---------------------------------------------------------------------------

def draw_zoom_tabs(stdscr, height: int, width: int,
                   panes: list, focused_idx: int, colors: dict) -> None:
    """
    Draw a tab bar on row (height-2) showing all pane labels.

    The focused pane tab is rendered in bold orange with [ brackets ].
    Inactive tabs are dim.  A hint on the right reminds the user how
    to navigate and exit zoom mode.

    Example:
      [ Q AAPL ] │  GIP TSLA  │  EMPTY       Tab = cycle   Z = zoom off
    """
    row = height - 2
    col = 1

    for i, pane in enumerate(panes):
        label = pane["activecommand"] or "EMPTY"

        if i == focused_idx:
            tab_text  = f" [ {label} ] "
            tab_color = colors["orange"] | curses.A_BOLD
        else:
            tab_text  = f"  {label}  "
            tab_color = colors["dim"]

        try:
            stdscr.attron(tab_color)
            stdscr.addstr(row, col, tab_text[: width - col - 1])
            stdscr.attroff(tab_color)
        except Exception:
            pass

        col += len(tab_text)

        if i < len(panes) - 1 and col < width - 2:
            try:
                stdscr.attron(colors["dim"])
                stdscr.addstr(row, col, "│")
                stdscr.attroff(colors["dim"])
            except Exception:
                pass
            col += 1

    hint = "Tab = cycle   Z = zoom off "
    hint_col = width - len(hint) - 1
    if hint_col > col + 2:
        try:
            stdscr.attron(colors["dim"])
            stdscr.addstr(row, hint_col, hint[: width - hint_col - 1])
            stdscr.attroff(colors["dim"])
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Focus indicator (shown above footer)
# ---------------------------------------------------------------------------

def draw_focus_indicator(stdscr, height: int, width: int,
                          focused_pane: int, n_panes: int,
                          input_focused: bool, colors: dict,
                          zoomed: bool = False) -> None:
    """
    Draw a small mode/focus hint just above the footer, right-aligned.

    INPUT mode:  [INPUT]  ` = pane mode
    PANE mode:   P1● P2○ P3○  [PANE 1]  ` = input  Z = zoom  Tab = cycle

    Suppressed in zoom mode — draw_zoom_tabs() serves that role instead.
    """
    if zoomed:
        return
    if n_panes < 2:
        return

    if input_focused:
        hint = "[INPUT MODE]   ` = switch to pane mode"
    else:
        parts = []
        for i in range(n_panes):
            dot = "●" if i == focused_pane else "○"
            parts.append(f"P{i + 1}{dot}")
        pane_str = "  ".join(parts)
        hint = f"{pane_str}   [PANE {focused_pane + 1}]   ` = input   Z = zoom   Tab = cycle"

    try:
        stdscr.attron(colors["dim"])
        stdscr.addstr(height - 2, max(0, width - len(hint) - 2), hint[: width - 2])
        stdscr.attroff(colors["dim"])
    except Exception:
        pass