# Weather Polymarket Bot

Automated prediction market trading bot that detects mispriced weather markets on Polymarket using probabilistic ensemble weather forecasting.

---

## How It Works

Most weather markets on Polymarket are priced by intuition. This bot prices them with physics — using the ECMWF ensemble model (51 atmospheric simulations) to derive a calibrated probability, then comparing it to the market price to find edges.

### The Core Idea

```
Market question:  "Will the highest temperature in Shanghai be >= 16°C on March 14?"
Market price:     YES = $0.42  →  implied probability = 42%

Bot forecast:     51 ensemble members → 32 of 51 exceed 16°C
Model probability: 32/51 = 62.7%

Edge:             62.7% - 42% = +20.7%  →  BUY YES
```

When the edge exceeds the threshold (default 10%), the bot sizes the position using fractional Kelly and executes.

---

## Architecture: Market-First Flow

The bot scans Polymarket **before** touching any weather API. This means forecasts are only computed for markets that actually exist — no wasted API calls.

```
┌─────────────────────────────────────────────────────┐
│  1. Polymarket Scanner                              │
│     Gamma API → active weather markets              │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│  2. Market Parser                                   │
│     "highest temp in Shanghai >= 16°C on March 14"  │
│     → city=Shanghai, date=2026-03-14,               │
│       threshold=16, condition=>=                    │
└───────────────────────┬─────────────────────────────┘
                        │ (only for parseable markets)
                        ▼
┌─────────────────────────────────────────────────────┐
│  3. Open-Meteo Ensemble Forecast  (free, no key)    │
│     Geocode city → lat/lon                          │
│     ECMWF IFS 51-member ensemble for target date    │
│     → [15.2, 16.4, 17.1, 16.8, 18.0, 14.9, ...]   │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│  4. Probability Engine                              │
│     P(temp >= 16) = count(members >= 16) / 51       │
│     → 62.7%                                         │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│  5. Edge Detector                                   │
│     edge = model_prob - market_price                │
│     62.7% - 42% = +20.7%  →  BUY YES  [HIGH]       │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│  6. Fractional Kelly Sizer                          │
│     f* = (b·p - q) / b  ×  0.25  ×  bankroll       │
│     → $18.40                                        │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│  7. Risk Manager                                    │
│     - max 3% per market                             │
│     - max 10% per city                              │
│     - max 5% daily loss stop-loss                   │
│     - max 25% total exposure                        │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│  8. Trader                                          │
│     dry_run=true  → logs trade, no real order       │
│     dry_run=false → py-clob-client limit order      │
└─────────────────────────────────────────────────────┘
```

---

## Project Structure

```
weather-polymarket-bot/
├── main.py                        # Entry point and main bot loop
├── config.yaml                    # All tunable parameters
├── requirements.txt
├── .env.example                   # Credentials template
│
├── market/
│   ├── polymarket_client.py       # Scans Gamma API for weather markets
│   └── market_parser.py           # Parses question text → structured data
│
├── weather/
│   ├── forecast.py                # Open-Meteo geocoding + ensemble fetch
│   └── probability.py             # Derives P(condition) from ensemble members
│
├── trading/
│   ├── edge_detector.py           # model_prob − market_price + confidence
│   ├── position_sizer.py          # Fractional Kelly criterion
│   └── risk_manager.py            # Exposure limits + state.json persistence
│
├── execution/
│   └── trader.py                  # Dry-run logger or live CLOB orders
│
└── utils/
    └── logger.py                  # Loguru console + rotating file output
```

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env`:

```env
BANKROLL=1000          # starting bankroll in USDC

# Required only for live trading:
POLY_PRIVATE_KEY=      # your Polygon wallet private key (0x...)
POLY_API_KEY=          # Polymarket CLOB API key
POLY_API_SECRET=       # Polymarket CLOB API secret
POLY_API_PASSPHRASE=   # Polymarket CLOB API passphrase
```

> The bot runs in dry-run mode by default. You do not need any credentials to test it.

### 3. Review config

Key settings in `config.yaml`:

```yaml
trading:
  edge_threshold: 0.10    # minimum edge required to trade (10%)
  kelly_fraction: 0.25    # bet 25% of full Kelly for safety
  dry_run: true           # set to false for live trading

risk:
  max_exposure_per_market: 0.03   # 3% of bankroll per market
  max_exposure_per_city: 0.10     # 10% of bankroll per city
  max_daily_loss: 0.05            # stop trading if down 5% today
  max_portfolio_exposure: 0.25    # max 25% of bankroll deployed

weather:
  ensemble_models:
    - ecmwf_ifs04         # ECMWF IFS 51-member ensemble (free)
  max_forecast_days: 14   # skip markets more than 14 days out
```

---

## Running

```bash
# Single cycle — good for testing
python main.py --once

# Single cycle, force dry-run regardless of config
python main.py --once --dry-run

# Continuous loop (every 5 minutes by default)
python main.py

# Custom config file
python main.py --config my_config.yaml
```

### Example output

```
2026-03-15 10:30:00 | INFO     | Found 12 weather markets on Polymarket
2026-03-15 10:30:01 | INFO     | Market: "Will the highest temperature in Shanghai be >= 16°C on March 14?" | Parsed: city=Shanghai date=2026-03-14 threshold=16.0C condition=>=
2026-03-15 10:30:02 | DEBUG    | Geocoded 'Shanghai' → lat=31.22, lon=121.46
2026-03-15 10:30:03 | DEBUG    | Ensemble stats: n=51, mean=16.8, std=1.4 | P(value >= 16.0) = 34/51 = 0.667
2026-03-15 10:30:03 | INFO     | Edge analysis: model=0.667 market=0.420 edge=+0.247 → BUY_YES [HIGH]
2026-03-15 10:30:03 | INFO     | TRADE SIGNAL: BUY YES | city=Shanghai date=2026-03-14 threshold=16.0C | model=66.7% market=42.0% edge=+24.7% | size=$18.40 @ 0.420
2026-03-15 10:30:03 | INFO     | [DRY RUN] BUY YES | market=abc123... | size=$18.40 @ 0.420 | shares=43.81
2026-03-15 10:30:03 | SUCCESS  | Trade executed: YES $18.40 on "Will the highest temperature in Shanghai..."
2026-03-15 10:30:08 | INFO     | Cycle complete: 1 trades, 11 skipped, 0 errors | Total exposure: $18.40 | Daily PnL: $0.00
```

---

## Weather Forecast Details

### Data source

[Open-Meteo](https://open-meteo.com) — free, no API key, no rate limits for reasonable use.

### Model

**ECMWF IFS 04** (`ecmwf_ifs04`) — 51-member ensemble, 0.4° resolution, up to 15 days ahead.

Each member represents a plausible atmospheric trajectory. The spread across members captures forecast uncertainty.

### Variables supported

| Market type | API variable | Units |
|---|---|---|
| Daily high temperature | `temperature_2m_max` | °C |
| Daily low temperature | `temperature_2m_min` | °C |
| Total precipitation | `precipitation_sum` | mm |
| Snowfall | `snowfall_sum` | cm |

### Probability derivation

```python
# Example: P(temp >= 16°C) from 51 ensemble members
ensemble = [15.2, 16.4, 17.1, 16.8, 18.0, 14.9, ...]
threshold = 16.0
hits = sum(1 for v in ensemble if v >= threshold)  # = 34
probability = hits / len(ensemble)                  # = 34/51 = 0.667
```

---

## Trading Logic

### Edge calculation

```
edge = model_probability - market_yes_price

edge > +10%  →  BUY YES  (market underprices YES)
edge < -10%  →  BUY NO   (market underprices NO)
otherwise    →  PASS
```

### Position sizing (Fractional Kelly)

Full Kelly formula for binary outcomes:

```
b = (1 - price) / price    # net payout odds
f* = (b·p - q) / b         # Kelly fraction

position_size = 0.25 × f* × bankroll
```

Example with model=62.7%, market=42%:

```
b = (1 - 0.42) / 0.42 = 1.381
f* = (1.381 × 0.627 - 0.373) / 1.381 = 0.367
size = 0.25 × 0.367 × $1000 = $91.75
→ capped at $30 by per-market limit (3% of $1000)
```

Quarter Kelly (0.25×) is used as a safety multiplier to account for model uncertainty and parameter estimation error.

### Confidence tiers

| Tier | Edge | Effect |
|---|---|---|
| HIGH | ≥ 20% | Full sized position |
| MEDIUM | 12–20% | Full sized position |
| LOW | 10–12% | Full sized, but logged as low-confidence |

Confidence is also downgraded if ensemble spread > 5°C (highly uncertain forecast).

---

## Risk Management

All limits are checked before every trade. State persists across restarts in `state.json`.

| Limit | Default | Description |
|---|---|---|
| Per-market | 3% | Max bankroll allocated to any single market |
| Per-city | 10% | Max total exposure to markets in the same city |
| Daily stop-loss | 5% | Bot stops trading for the day if down this much |
| Total exposure | 25% | Max fraction of bankroll deployed at once |

Positions are capped (not rejected) when limits allow partial sizing. The bot will not re-enter a market it already holds a position in.

---

## Live Trading Setup

> **Start with dry-run and verify the bot is finding real edges before going live.**

1. Create a Polymarket account and fund with USDC on Polygon
2. Generate CLOB API credentials from the Polymarket settings
3. Fill in all credentials in `.env`
4. Set `dry_run: false` in `config.yaml`
5. Start small: set `BANKROLL=100` until you validate performance

```bash
python main.py --once    # verify one cycle works live
python main.py           # run continuously
```

---

## State File

`state.json` tracks portfolio state between runs:

```json
{
  "positions": {
    "market_abc123": {
      "city": "Shanghai",
      "side": "YES",
      "size": 18.40,
      "price": 0.420,
      "token_id": "0x...",
      "date": "2026-03-15"
    }
  },
  "city_exposure": {
    "Shanghai": 18.40
  },
  "total_exposure": 18.40,
  "daily_pnl": 0.0,
  "date": "2026-03-15"
}
```

Delete `state.json` to reset all tracked positions (does not affect actual on-chain positions).

---

## Logs

Logs are written to `bot.log` (rotating, 10 MB max, 7 days retention) and printed to console.

To change verbosity, edit `config.yaml`:

```yaml
bot:
  log_level: DEBUG    # DEBUG | INFO | WARNING | ERROR
```

---

## Limitations

- **Ensemble resolution**: ECMWF IFS 04 is 0.4° (~40 km). For small cities or complex terrain, forecast uncertainty is higher.
- **Forecast horizon**: Ensemble forecasts degrade beyond ~10 days. The bot skips markets further out than `max_forecast_days` (default 14).
- **Market parsing**: The regex parser handles common question formats. Unusual phrasings may fail to parse and be silently skipped.
- **Single entry per market**: The bot does not average into positions or manage open positions. It takes one entry and leaves it.
- **No exit logic**: The bot does not close positions. Polymarket positions resolve automatically at market close.

---

## Dependencies

| Package | Purpose |
|---|---|
| `requests` | HTTP calls to Polymarket and Open-Meteo APIs |
| `python-dotenv` | Load credentials from `.env` |
| `pyyaml` | Parse `config.yaml` |
| `schedule` | Bot loop timing |
| `loguru` | Structured logging |
| `py-clob-client` | Live order placement on Polymarket CLOB |

---

## Disclaimer

This software is for educational and research purposes. Prediction market trading carries financial risk. Past model accuracy does not guarantee future returns. Use at your own risk.
