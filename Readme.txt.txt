# Brodberg Terminal

A Bloomberg-style financial terminal built in Python with live market data,
AIS vessel tracking, online user accounts, and a multi-pane curses UI.
Runs as a standalone .exe or directly with Python.

## Features

- Live quotes, price charts, and financial statements
- Commodities, FX majors, and U.S. Treasury yield curve dashboards
- Live AIS ship tracking at the Strait of Hormuz
- Online user accounts — register, login, and view profiles
- Multi-pane layout — run up to 3 commands side by side
- All market data routed through the Brodberg server (no API keys needed by users)

## Run from source

  pip install -r requirements.txt
  python main.py

## Build .exe

  pyinstaller --onefile --icon=brodberg_icon.ico --add-data "data;data" --add-data "docs;docs" --add-data "brodberg_icon.ico;." --name Brodberg main.py

## Commands

  Market Data
  -----------
  Q <TICKER>                      Live quote
  GIP <TICKER> [1W|1M|3M|YTD|1Y] Price chart (default: 1Y)
  DES <TICKER>                    Company description and profile
  FA <TICKER> [IS|BS|CF] [ANNUAL] Financial statements
  RATES                           U.S. Treasury yield curve
  COMD                            Commodities dashboard (Energy, Metals, Grains)
  FX [G10|EM]                     FX major pairs vs USD
  SHIP HORMUZ                     Live AIS vessel tracking

  Account
  -------
  REGISTER <username> <password>  Create an account
  LOGIN <username> <password>     Sign in
  LOGOUT                          Sign out
  PROFILE                         View your profile
  PROFILE <username>              View another user's profile

  General
  -------
  HELP                            List all commands
  CL                              Changelog
  CLEAR                           Clear the active pane
  EXIT                            Close the terminal

## Navigation

  ` (backtick)   Toggle INPUT mode / PANE mode

  INPUT mode     All keystrokes go to the command bar
  PANE mode      Z = zoom   Tab = cycle pane   ← → = switch tab/timeframe

## Infrastructure

  Server:   https://brodberg.onrender.com  (Render free tier — Python + FastAPI)
  Database: Render managed PostgreSQL
  Repo:     https://github.com/JackBroderick/brodberg

  The server proxies all Finnhub and AISStream API calls.
  API keys live only in Render environment variables — never in client code.

## Project Structure

  BrodBerg/
  ├── main.py                  Entry point and curses loop
  ├── market_data.py           server_get() helper + benchmark/news threads
  ├── brodberg_session.py      Local session management (~/.brodberg/session.json)
  ├── chart.py                 Price chart rendering
  ├── ship_data.py             AIS WebSocket client
  ├── requirements.txt         Client dependencies
  ├── server/
  │   ├── main.py              FastAPI server (accounts + API proxy)
  │   └── requirements.txt     Server dependencies
  ├── data/
  │   └── hormuz.txt           Strait of Hormuz map
  ├── docs/
  │   ├── HelpMenu.txt         In-terminal help text
  │   └── ChangeLog.txt        Version history
  ├── ui/
  │   ├── colors.py            Color pair definitions
  │   └── chrome.py            Header, footer, pane drawing
  └── commands/
      ├── registry.py          Command router
      ├── cmd_auth.py          REGISTER LOGIN LOGOUT PROFILE
      ├── cmd_quote.py         Q
      ├── cmd_gip.py           GIP
      ├── cmd_des.py           DES
      ├── cmd_fa.py            FA
      ├── cmd_ship.py          SHIP
      ├── cmd_rates.py         RATES
      ├── cmd_comd.py          COMD
      ├── cmd_fx.py            FX
      ├── cmd_help.py          HELP
      ├── cmd_changelog.py     CL
      └── cmd_error.py         Error display
