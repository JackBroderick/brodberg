# Brodberg Terminal

Terminal built in Python with live market data, 
AIS vessel tracking, and a multi-pane curses UI.

## Features
- Live quotes, price charts, and financial statements via Finnhub
- Commodities, FX, and Treasury yield curve dashboards
- Live AIS ship tracking at the Strait of Hormuz
- Multi-pane layout — run up to 3 commands side by side

## Setup
pip install -r requirements.txt
python main.py

## Commands
| Command | Description |
|---------|-------------|
| Q <TICKER> | Live quote |
| GIP <TICKER> <1W\|1M\|YTD\|1Y> | Price chart |
| DES <TICKER> | Security description |
| FA <TICKER> | Financial statements |
| RATES | Treasury yield curve |
| COMD | Commodities dashboard |
| FX | FX majors vs USD |
| SHIP HORMUZ | Live AIS vessel tracking |

## Navigation
| Key | Action |
|-----|--------|
| ` (backtick) | Toggle input / pane mode |
| Tab | Cycle focused pane |
| ↑ ↓ | Command history (input mode) / scroll (pane mode) |
```

---

**4.folder structure**
```
BrodBerg/
├── main.py
├── market_data.py
├── chart.py
├── ship_data.py
├── requirements.txt
├── README.md
├── data/
│   └── hormuz.txt
├── docs/
│   ├── HelpMenu.txt
│   └── ChangeLog.txt
├── ui/
│   ├── colors.py
│   └── chrome.py
└── commands/
    ├── registry.py
    ├── cmd_quote.py
    ├── cmd_gip.py
    ├── cmd_des.py
    ├── cmd_fa.py
    ├── cmd_ship.py
    ├── cmd_rates.py
    ├── cmd_comd.py
    ├── cmd_fx.py
    ├── cmd_help.py
    ├── cmd_changelog.py
    └── cmd_error.py