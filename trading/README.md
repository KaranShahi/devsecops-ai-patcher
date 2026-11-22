# XAU/USD price-action bot

Automates a multi-timeframe strategy on MetaTrader 5: **D1 bias → H1 zone → M5 tap → M5 change of character → 1:2 trade**.
Includes a backtester, so you can test the rules on history before any order is placed.

> **Status:** the rules engine, backtester and live executor are tested against synthetic data and a *fake* MT5.
> It has **not** been run against a real MT5 terminal or real gold data. Nothing here shows the strategy is profitable.
> Backtest first, then run on a **demo** account for weeks, and only then consider real money at small size.

## The rules as implemented (`xauusd_bot/strategy.py`)

| # | Your rule | How it is coded |
|---|---|---|
| 1 | D1 structure: up → buy, down → sell, sideways → no trade | Last two D1 swing highs/lows (2 bars each side). HH+HL = up, LH+LL = down, otherwise (or if the latest close breaks the last swing, or moves are < 0.25 × daily ATR) = sideways. |
| 2 | Candle quality | Over the last 5 D1 candles: *size* = avg range ÷ 60-day median range, *wick* = avg (wick ÷ range). Size ≥ 1.2 and wick ≥ 0.40 → **A** (full risk). Size ≥ 0.8 and wick ≥ 0.25 → **B** (half risk). Otherwise (small candles) → skip. |
| 3–4 | H1 key level + drag a box | Most recent H1 swing high (uptrend) that price has already **closed above**, so it is now support. Box = from that high down 0.5 × H1 ATR. Downtrend mirrors this with the last broken swing low, box above it. Zone is dropped if H1 closes through it or after 120 H1 bars. |
| 5 | M5 tap, shouldn't break in | First M5 bar whose low reaches the box. Invalid if any M5 close goes beyond the far edge of the box afterwards. |
| 6 | Change of character | After the tap, take the lowest low, then the last M5 swing high before it (the "lower high"). Signal = the first M5 close above that high. Must happen within 48 bars (4 h) of the tap. Mirrored for sells. |
| 7 | Stop below structure, 1:2 | Stop = structure low − 0.25 × M5 ATR. Take profit = 2 × risk. Setups with a stop under $1 or over $25 are skipped. |

Also: New York session filter (12:00–20:00 UTC), one trade at a time, one trade per zone, max 2 trades/day, 2% daily loss limit, 0.5% risk per trade.

### Interpretation choices to check against your own trading
These are where I had to guess; each has a setting in `config.py`.
* **"Look at the last high and drag a box"**: I used a swing high that has been *broken* (break-and-retest). If you mean something else (e.g. the high that price is still below), tell me.
* **Candle-quality thresholds** (1.2 / 0.8 size, 0.40 / 0.25 wick) are my numbers for "big / decent / small".
* **"Last high"** uses 3-bar swings on H1, and 2-bar swings on M5/D1.
* **Server time:** `broker_utc_offset_hours` (default +2) must match your broker, or the session filter is shifted. Brokers shift this by an hour with US daylight saving.

## Setup (Windows laptop with MT5)

```
pip install -r requirements.txt
```
Open MT5, log in (use a **demo** account first), and make sure `XAUUSD` shows in Market Watch. Some brokers name it `XAUUSDm`, `GOLD`, etc.; set `symbol` in `config.json`.

### 1. Get history and backtest
```
python -m xauusd_bot fetch --bars 100000 --config config.json     # ~1 year of M5 -> data/xauusd_m5.csv
python -m xauusd_bot backtest --data data/xauusd_m5.csv --config config.json
```
(MT5's own *Symbols → Bars → Export* CSV also works.) Results land in `results/trades.csv` and `equity.csv`.
H1 and D1 are built from the M5 file, so one file is enough. The first ~40 days are warm-up.
Look at the number of trades (dozens at least), profit factor, drawdown, and whether results hold on a different time slice. Tune the thresholds on one period and check them on another.

### 2. Dry run (signals only, no orders)
```
python -m xauusd_bot live --config config.json
```
Logs to `bot.log` what it *would* trade. Leave it running during the New York session.

### 3. Demo orders, then real
```
python -m xauusd_bot live --live --config config.json                 # demo account
python -m xauusd_bot live --live --allow-real --config config.json    # real account: refused without the flag
```
Safeguards: every order has a stop and target; `order_check` runs before sending; spread filter; one open trade; daily trade and loss caps; state in `bot_state.json` prevents re-trading the same zone after a restart; **create a file named `STOP` to halt it**. The laptop has to stay on, awake and connected. Consider a VPS later.

## Tests
```
python -m pytest -q
```
Includes a no-look-ahead test (truncating the data doesn't change earlier trades) and a mirror test (the short side is the exact mirror of the long side).

## Backtest assumptions
Signal on bar close, fill at the next open; spread of $0.30 added on the ask side; if stop and target are in the same M5 bar the stop wins; no swap or slippage beyond the spread. Real fills will be worse around news.
