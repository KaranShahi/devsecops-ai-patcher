# Telegram → MT5 Gold Signal Copier

Listens to your Telegram signal channel(s), parses XAUUSD/GOLD signals, and places the trades in MetaTrader 5 automatically.

## Setup (Windows PC with MT5 installed and logged in)
1. `pip install -r requirements.txt` (the `MetaTrader5` package only works on Windows)
2. Get a Telegram `api_id` / `api_hash` at https://my.telegram.org → *API development tools*
3. `copy config.example.ini config.ini` and fill it in (channel name, your broker's gold symbol e.g. `XAUUSD.m`)
4. In MT5: **Tools → Options → Expert Advisors → Allow algorithmic trading**
5. `python main.py` — first run asks for your phone number + Telegram login code.

## Safety defaults
- `dry_run = true`: parses and logs only. Watch `copier.log` for a few days, then set it to `false`.
- **Test on a demo account first.** Real accounts are refused unless `allow_live_account = true`.
- Signals without a stop loss, with SL/TP on the wrong side, or for other instruments are ignored.
- Risk-based lot sizing (`risk_percent` of balance), `max_lots_per_signal`, `max_open_trades`, spread filter, stale-message filter, duplicate protection.
- **Layered entries** (your rule #1): a zone like `SELL 3622–3631` becomes up to `max_layers` limit orders ~35 pips apart (`3622 / 3626.5 / 3631`). Your total risk is *divided* across the layers, so layering never adds risk. Layers price has already passed are skipped, never chased.
- **TP1 management** (your rule #3): when price hits TP1 the bot closes `tp1_close_percent` of each position (or everything with `tp1_action = full`) and moves the rest to breakeven (+ a small buffer for spread/commission). The rest then runs to `hold_tp` (default TP2) or the SL.
- **"Close your worst entry"**: a short message like that closes the worst-priced open layer (lowest sell / highest buy).
- Long announcements and "for example…" messages are never treated as signals.

## Supported signal formats
```
GOLD BUY NOW 2650.5  SL 2640  TP1 2660  TP2 2670
XAUUSD SELL @ 2655-2658  SL 2665  TP 2645
Buy limit gold 2,650  Stop loss: 2640  Take profit: 2670
```
Different format? Adjust the regexes in `signal_parser.py` and add a test in `tests/test_parser.py` (`pytest tests`).

## Notes
- Your PC and MT5 must stay on while it runs (a VPS is common).
- Signals carry no guarantee of profit; automation also executes bad signals instantly. Use small risk.
- Never commit `config.ini` or `*.session` (git-ignored) — the session file grants access to your Telegram account.
