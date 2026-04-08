"""
main.py
-------
Brodberg Terminal — entry point and main curses loop.

This file should stay clean. It only handles:
  - Terminal setup / color init
  - The render loop (header, banners, active panes)
  - Input handling (keystrokes → command string or pane navigation)
  - Delegating to the command registry

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FOCUS MODEL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  `  (backtick)  — toggle between INPUT mode and PANE mode

  INPUT mode  — ALL keystrokes go to the command bar.
                Pressing Enter submits the command and
                automatically switches to PANE mode —
                UNLESS the command was unrecognised, in
                which case focus stays in INPUT mode so
                the user can correct their typo.

  PANE mode   — keystrokes navigate panes:
                  Z      toggle zoom (focused pane full-screen)
                  Tab    cycle focused pane
                  ↑ ↓ ← →  route to active command's on_keypress()

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW TO ADD A NEW COMMAND
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Create  commands/<your_command>.py  with fetch() + render()
  2. Register it in commands/registry.py
  3. Add a help line to HelpMenu.txt
  main.py never needs to change.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import curses
import datetime
import time
import sys
import ctypes
import os

import market_data
from ui.colors import init_colors
from ui.chrome import (
    draw_header,
    draw_footer,
    draw_panes,
    draw_focus_indicator,
    draw_zoom_tabs,
)
from commands.registry import process_command, dispatch_render, dispatch_keypress

if sys.platform == "win32":
    ctypes.windll.kernel32.SetConsoleTitleW("Brodberg Terminal")

MAX_PANES  = 3
PROMPT     = " Command > "
PROMPT_LEN = len(PROMPT)


# ---------------------------------------------------------------------------
# PyInstaller-safe resource path
# ---------------------------------------------------------------------------

def resource_path(relative: str) -> str:
    """
    Resolve a path to a bundled data file whether running as:
      - a raw Python script  (base = directory of main.py)
      - a PyInstaller .exe   (base = sys._MEIPASS temp extraction folder)
    """
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)


# ---------------------------------------------------------------------------
# Console icon (Windows only, best-effort)
# ---------------------------------------------------------------------------

def _set_console_icon(ico_path: str) -> None:
    """
    Set the console window icon on Windows via WM_SETICON.
    Works in cmd.exe / conhost.exe.
    Silently ignored in Windows Terminal (which controls its own chrome).
    Never raises — a missing icon must never crash the terminal.
    """
    if sys.platform != "win32":
        return
    try:
        ico_abs = resource_path(ico_path)
        if not os.path.isfile(ico_abs):
            return
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return
        hicon_big = ctypes.windll.user32.LoadImageW(
            None, ico_abs, 1,       # IMAGE_ICON
            0, 0,
            0x10 | 0x0040,          # LR_LOADFROMFILE | LR_DEFAULTSIZE
        )
        hicon_small = ctypes.windll.user32.LoadImageW(
            None, ico_abs, 1,
            16, 16,
            0x10,                   # LR_LOADFROMFILE
        )
        WM_SETICON = 0x0080
        ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 1, hicon_big)
        ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, 0, hicon_small)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main(stdscr):
    # ── Terminal setup ────────────────────────────────────────────────────
    curses.curs_set(0)   # hide hardware cursor — we draw our own below
    curses.noecho()
    stdscr.keypad(True)
    stdscr.nodelay(True)
    stdscr.timeout(100)        # 100 ms tick — drives scroll animation

    colors = init_colors()
    sys.stdout.reconfigure(encoding="utf-8")

    stdscr.bkgd(" ", colors["orange"])

    # Start background data threads
    market_data.start_benchmark_thread()
    market_data.start_news_thread()

    # ── Pane state ────────────────────────────────────────────────────────
    # Each pane: {"activecommand": str, "cache": dict}
    panes = [{"activecommand": "", "cache": {}} for _ in range(MAX_PANES)]

    focused_pane  = 0      # which pane receives commands / keypresses
    input_focused = True   # True  = INPUT mode  (all keys go to command bar)
                           # False = PANE mode   (Z / Tab / arrows navigate)
    zoomed        = True   # True  = focused pane fills screen, others as tabs

    # ── Input state ───────────────────────────────────────────────────────
    command  = ""
    running  = True
    history  = []
    hist_idx = -1

    scroll_offset     = 0
    last_scroll_time  = time.monotonic()

    while running:
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        now = datetime.datetime.now()

        # ── Fixed chrome (rows 0-3) ───────────────────────────────────────
        draw_header(stdscr, width, now, colors)
        market_data.draw_benchmark_banner(stdscr, width, colors)
        market_data.draw_news_ticker(stdscr, width, scroll_offset, colors)

        # ── Pane content ──────────────────────────────────────────────────
        draw_panes(stdscr, panes, focused_pane, colors, dispatch_render,
                   zoomed=zoomed)

        # ── Zoom tab bar (zoom mode only) ─────────────────────────────────
        if zoomed:
            draw_zoom_tabs(stdscr, height, width, panes, focused_pane, colors)

        # ── Focus indicator + footer ──────────────────────────────────────
        draw_focus_indicator(stdscr, height, width,
                             focused_pane, MAX_PANES, input_focused, colors,
                             zoomed=zoomed)
        # Software cursor — blinks every 500 ms, unaffected by loop speed
        cursor_blink = int(time.monotonic() * 2) % 2 == 0
        draw_footer(stdscr, height, width, command, colors, input_focused,
                    cursor_blink=cursor_blink)

        stdscr.refresh()
        now_mono = time.monotonic()
        if now_mono - last_scroll_time >= 0.1:
            scroll_offset    += market_data.NEWS_SCROLL_SPEED
            last_scroll_time  = now_mono

        # ── Input ─────────────────────────────────────────────────────────
        key = stdscr.getch()

        # ── ` (backtick) — the ONE global key: toggle input / pane mode ───
        if key == ord("`"):
            input_focused = not input_focused

        # ══════════════════════════════════════════════════════════════════
        # INPUT MODE — every keystroke goes to the command bar
        # ══════════════════════════════════════════════════════════════════
        elif input_focused:

            if key in (curses.KEY_BACKSPACE, 127, 8):
                command  = command[:-1]
                hist_idx = -1

            elif key in (curses.KEY_ENTER, 10, 13):
                if command.strip():
                    history.append(command.strip())
                hist_idx = -1
                result   = process_command(command)
                running  = result[0]
                active   = result[1]
                cache    = result[2]
                panes[focused_pane]["activecommand"] = active
                panes[focused_pane]["cache"]         = cache
                command = ""
                # Auto-switch to pane mode on a recognised command so the
                # user can immediately interact with the result.
                # Stay in input mode on an error so the user can retype.
                if not active.startswith("ERROR:"):
                    input_focused = False

            elif key == curses.KEY_UP:
                if history:
                    hist_idx = (len(history) - 1
                                if hist_idx == -1
                                else max(0, hist_idx - 1))
                    command = history[hist_idx]

            elif key == curses.KEY_DOWN:
                if hist_idx == -1:
                    pass
                elif hist_idx >= len(history) - 1:
                    hist_idx = -1
                    command  = ""
                else:
                    hist_idx += 1
                    command  = history[hist_idx]

            elif 32 <= key <= 126:
                command += chr(key)
                hist_idx = -1

        # ══════════════════════════════════════════════════════════════════
        # PANE MODE — navigation keys control panes, not the command bar
        # ══════════════════════════════════════════════════════════════════
        else:
            pane      = panes[focused_pane]
            form_mode = pane["cache"].get("form_mode", False)

            if form_mode and key != -1:
                # Active pane has an interactive form — route ALL keystrokes
                # to its on_keypress handler (typing, backspace, Enter, arrows).
                pane["cache"] = dispatch_keypress(
                    key,
                    pane["activecommand"],
                    pane["cache"],
                )

            else:
                # Z — toggle zoom
                if key in (ord("z"), ord("Z")):
                    zoomed = not zoomed

                # Tab — cycle focused pane
                elif key == 9:
                    stdscr.nodelay(True)
                    while stdscr.getch() != -1:
                        pass
                    focused_pane = (focused_pane + 1) % MAX_PANES

                # Arrow keys + Enter — route to active command's on_keypress handler
                elif key in (curses.KEY_UP, curses.KEY_DOWN,
                             curses.KEY_LEFT, curses.KEY_RIGHT,
                             curses.KEY_ENTER, 10, 13):
                    pane["cache"] = dispatch_keypress(
                        key,
                        pane["activecommand"],
                        pane["cache"],
                    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_set_console_icon("brodberg_icon.ico")
curses.wrapper(main)