"""
commands/registry.py
--------------------
Central command router for the Brodberg Terminal.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW TO REGISTER A NEW COMMAND
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Create  commands/<your_command>.py  with:
       fetch(parts)  -> dict
       render(stdscr, cache, colors) -> None
       on_keypress(key, cache) -> dict   ← optional; return updated cache

  2. Import it below and add an entry to COMMAND_REGISTRY:
       {
           "prefix":      "MYPREFIX",   # matched against upper-case input
           "exact":       False,        # True  = full string must match
                                        # False = startswith match
           "fetch":       my_module.fetch,
           "render":      my_module.render,
           "on_keypress": my_module.on_keypress,  # omit if not needed
       }

  3. Add a help line to HelpMenu.txt.

  main.py never needs to change — this file is the only edit point.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ARROW KEY NAVIGATION CONTRACT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  main.py should call dispatch_keypress(stdscr, key, activecommand, cache)
  whenever it receives curses.KEY_LEFT / KEY_RIGHT / KEY_UP / KEY_DOWN.

  on_keypress(key, cache) -> dict
    - Receives the raw curses key constant and the current cache dict.
    - Returns the new cache dict.
    - If only display state changes (e.g. FA tab switch), mutate and return
      cache directly — no API call needed.
    - If new data is required (e.g. GIP timeframe change), call fetch()
      internally and return its result.
    - Must never block for more than a few milliseconds; heavy fetches
      should be done in a background thread if latency is a concern.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from commands import cmd_quote
from commands import cmd_gip
from commands import cmd_help
from commands import cmd_changelog
from commands import cmd_des
from commands import cmd_error
from commands import cmd_fa
from commands import cmd_ship
from commands import cmd_rates
from commands import cmd_comd
from commands import cmd_fx
from commands import cmd_user
from commands import cmd_ipo
from commands import cmd_news
from commands import cmd_peers
from commands import cmd_exec
from commands import cmd_div
from commands import cmd_own
from commands import cmd_sent
from commands import cmd_uo
from commands import cmd_earn
from commands import cmd_omon
from commands import cmd_chat

# ---------------------------------------------------------------------------
# Registry — ordered list of command descriptors.
# Entries are matched top-to-bottom; first match wins.
# ---------------------------------------------------------------------------

COMMAND_REGISTRY = [
    {
        "prefix":      "HELP",
        "exact":       True,
        "fetch":       cmd_help.fetch,
        "render":      cmd_help.render,
        "on_keypress": cmd_help.on_keypress,
    },
    {
        "prefix": "CL",
        "exact":  True,
        "fetch":  cmd_changelog.fetch,
        "render": cmd_changelog.render,
    },
    {
        "prefix": "Q",
        "exact":  False,
        "fetch":  cmd_quote.fetch,
        "render": cmd_quote.render,
    },
    {
        "prefix": "GIP",
        "exact":       False,
        "fetch":       cmd_gip.fetch,
        "render":      cmd_gip.render,
        "on_keypress": cmd_gip.on_keypress,
    },
    {
        "prefix": "DES",
        "exact":  False,
        "fetch":  cmd_des.fetch,
        "render": cmd_des.render,
    },
    {
        "prefix": "FA",
        "exact":       False,
        "fetch":       cmd_fa.fetch,
        "render":      cmd_fa.render,
        "on_keypress": cmd_fa.on_keypress,
    },
    {
        "prefix": "SHIP",
        "exact":  False,
        "fetch":  cmd_ship.fetch,
        "render": cmd_ship.render,
    },
    {
        "prefix": "RATES",
        "exact":  True,
        "fetch":  cmd_rates.fetch,
        "render": cmd_rates.render,
    },
    {
        "prefix": "COMD",
        "exact":  True,
        "fetch":  cmd_comd.fetch,
        "render": cmd_comd.render,
    },
    {
        "prefix": "FX",
        "exact":       False,
        "fetch":       cmd_fx.fetch,
        "render":      cmd_fx.render,
        "on_keypress": cmd_fx.on_keypress,
    },
    # ── User account command ─────────────────────────────────────────────────
    {
        "prefix":      "USER",
        "exact":       False,
        "fetch":       cmd_user.fetch,
        "render":      cmd_user.render,
        "on_keypress": cmd_user.on_keypress,
    },
    # ── News feed ────────────────────────────────────────────────────────────
    {
        "prefix":      "N",
        "exact":       False,
        "fetch":       cmd_news.fetch,
        "render":      cmd_news.render,
        "on_keypress": cmd_news.on_keypress,
    },
    # ── IPO Calendar ─────────────────────────────────────────────────────────
    {
        "prefix":      "IPO",
        "exact":       False,
        "fetch":       cmd_ipo.fetch,
        "render":      cmd_ipo.render,
        "on_keypress": cmd_ipo.on_keypress,
    },
    # ── Fundamental / ownership data ─────────────────────────────────────────
    {
        "prefix": "PEERS",
        "exact":  False,
        "fetch":  cmd_peers.fetch,
        "render": cmd_peers.render,
    },
    {
        "prefix": "EXEC",
        "exact":  False,
        "fetch":  cmd_exec.fetch,
        "render": cmd_exec.render,
    },
    {
        "prefix": "DIV",
        "exact":  False,
        "fetch":  cmd_div.fetch,
        "render": cmd_div.render,
    },
    {
        "prefix": "OWN",
        "exact":  False,
        "fetch":  cmd_own.fetch,
        "render": cmd_own.render,
    },
    {
        "prefix": "SENT",
        "exact":  False,
        "fetch":  cmd_sent.fetch,
        "render": cmd_sent.render,
    },
    # ── Unusual Options Activity ──────────────────────────────────────────
    {
        "prefix":      "UO",
        "exact":       True,
        "fetch":       cmd_uo.fetch,
        "render":      cmd_uo.render,
        "on_keypress": cmd_uo.on_keypress,
    },
    # ── Options Chain ─────────────────────────────────────────────────────
    {
        "prefix":      "OMON",
        "exact":       False,
        "fetch":       cmd_omon.fetch,
        "render":      cmd_omon.render,
        "on_keypress": cmd_omon.on_keypress,
    },
    # ── Earnings Calendar ─────────────────────────────────────────────────
    {
        "prefix":      "EARN",
        "exact":       True,
        "fetch":       cmd_earn.fetch,
        "render":      cmd_earn.render,
        "on_keypress": cmd_earn.on_keypress,
    },
    # ── Chat ──────────────────────────────────────────────────────────────
    {
        "prefix":      "CHAT",
        "exact":       False,
        "fetch":       cmd_chat.fetch,
        "render":      cmd_chat.render,
        "on_keypress": cmd_chat.on_keypress,
    },
]

# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def _match(upper: str, entry: dict) -> bool:
    if entry["exact"]:
        return upper == entry["prefix"]
    return upper == entry["prefix"] or upper.startswith(entry["prefix"] + " ")


def process_command(raw: str) -> tuple:
    """
    Parse raw input and return (running, activecommand, cache).

      running       — False means the app should exit
      activecommand — normalised upper-case command token stored by main.py
      cache         — data dict returned by the command's fetch(), or {}
    """
    upper = raw.strip().upper()

    if upper == "":
        return True, "", {}
    if upper == "CLEAR":
        return True, "", {}
    if upper == "EXIT":
        return False, "", {}

    for entry in COMMAND_REGISTRY:
        if _match(upper, entry):
            # Split on the original raw string so that passwords and other
            # case-sensitive arguments are preserved.  Only parts[0] (the
            # command name itself) is forced to uppercase.
            raw_parts   = raw.strip().split()
            fetch_parts = [raw_parts[0].upper()] + raw_parts[1:]
            cache = entry["fetch"](fetch_parts)
            return True, upper, cache

    cache = cmd_error.fetch(raw.strip())
    return True, f"ERROR:{raw.strip()}", cache


def dispatch_render(stdscr, activecommand: str, cache: dict, colors: dict) -> None:
    """
    Call the correct render() for the current active command.
    Invoked every frame — never hits the API.
    """
    if not activecommand:
        return

    upper = activecommand.upper()

    if upper.startswith("ERROR:"):
        cmd_error.render(stdscr, cache, colors)
        return

    for entry in COMMAND_REGISTRY:
        if _match(upper, entry):
            entry["render"](stdscr, cache, colors)
            return


def dispatch_keypress(key: int, activecommand: str, cache: dict) -> dict:
    """
    Route an arrow-key press to the active command's on_keypress() handler.

    Returns the (possibly updated) cache dict.  If the active command has no
    on_keypress handler the original cache is returned unchanged.

    Call this from main.py whenever curses.KEY_LEFT / KEY_RIGHT / KEY_UP /
    KEY_DOWN is received, e.g.:

        elif key in (curses.KEY_LEFT, curses.KEY_RIGHT,
                     curses.KEY_UP,   curses.KEY_DOWN):
            cache = registry.dispatch_keypress(key, activecommand, cache)
    """
    if not activecommand:
        return cache

    upper = activecommand.upper()

    for entry in COMMAND_REGISTRY:
        if _match(upper, entry) and "on_keypress" in entry:
            return entry["on_keypress"](key, cache)

    return cache