"""
watchlist_data.py
-----------------
Client access to the per-user watchlist stored on the Broderick Terminal server.

Public API (user must be logged in):
  get_watchlist()    -> list[dict]   each dict: {"ticker": str, "quote": dict}
  add_ticker(ticker) -> dict         {"ticker": str, "name": str}
  remove_ticker(ticker) -> None
"""

import requests
import broderick_session


def _headers() -> dict:
    return {"Authorization": f"Bearer {broderick_session.get_token()}"}


def _base() -> str:
    return broderick_session.get_server_url()


def get_watchlist() -> list:
    """Return watchlist with embedded Finnhub quotes for the logged-in user."""
    r = requests.get(f"{_base()}/watchlist", headers=_headers(), timeout=15)
    r.raise_for_status()
    return r.json().get("watchlist", [])


def add_ticker(ticker: str) -> dict:
    """
    Add ticker to watchlist. Returns {"ticker": str, "name": str}.
    Raises requests.HTTPError — check .response.json()["detail"] for the message.
    """
    r = requests.post(
        f"{_base()}/watchlist",
        json={"ticker": ticker.upper()},
        headers=_headers(),
        timeout=12,
    )
    r.raise_for_status()
    return r.json()


def remove_ticker(ticker: str) -> None:
    """Remove ticker from watchlist. Raises requests.HTTPError on failure."""
    r = requests.delete(
        f"{_base()}/watchlist/{ticker.upper()}",
        headers=_headers(),
        timeout=10,
    )
    r.raise_for_status()
