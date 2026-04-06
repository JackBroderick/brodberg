"""
commands/cmd_error.py
---------------------
Handles unknown / unrecognised commands.

This is NOT registered in COMMAND_REGISTRY — it is called directly by
registry.process_command() when no other entry matches.

  fetch(raw_command)            -> cache dict
  render(stdscr, cache, colors) -> None
"""


# ---------------------------------------------------------------------------
# fetch — called once on Enter (receives raw string, not parts list)
# ---------------------------------------------------------------------------

def fetch(raw_command: str) -> dict:
    return {"original": raw_command}


# ---------------------------------------------------------------------------
# render — called every frame
# ---------------------------------------------------------------------------

def render(stdscr, cache: dict, colors: dict) -> None:
    original = cache.get("original", "")
    try:
        stdscr.attron(colors["negative"])
        stdscr.addstr(
            4, 0,
            f"  Unknown command: '{original}'. Type HELP for a list of commands."
        )
        stdscr.attroff(colors["negative"])
    except Exception:
        pass
