"""Telegram -> MT5 gold signal copier.  Run: python main.py"""
from __future__ import annotations

import asyncio
import configparser
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient, events

from mt5_trader import Trader
from signal_parser import is_close_worst_command, parse_signal

HERE = Path(__file__).parent
SEEN_FILE = HERE / "seen_messages.json"

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(HERE / "copier.log", encoding="utf-8")],
)
log = logging.getLogger("copier")


def load_seen() -> set:
    try:
        return set(json.loads(SEEN_FILE.read_text()))
    except Exception:
        return set()


def main():
    cfg = configparser.ConfigParser()
    if not cfg.read(HERE / "config.ini"):
        sys.exit("config.ini not found - copy config.example.ini to config.ini and edit it.")

    trader = Trader(cfg)
    trader.connect()
    seen = load_seen()
    tg = cfg["telegram"]
    raw = [c.strip() for c in tg["channels"].split(",") if c.strip()]
    channels = [int(c) if c.lstrip("-").isdigit() else c for c in raw]
    max_age = cfg["trading"].getint("max_signal_age_seconds")

    client = TelegramClient(str(HERE / "tg_session"), int(tg["api_id"]), tg["api_hash"])

    @client.on(events.NewMessage(chats=channels))
    async def handler(event):
        key = f"{event.chat_id}:{event.id}"
        if key in seen:
            return
        seen.add(key)
        SEEN_FILE.write_text(json.dumps(sorted(seen)[-2000:]))
        age = (datetime.now(timezone.utc) - event.date).total_seconds()
        if age > max_age:
            log.warning("Ignoring stale message (%.0fs old)", age)
            return
        text = event.raw_text or ""
        loop = asyncio.get_running_loop()
        if cfg["trading"].getboolean("follow_manager_commands") and is_close_worst_command(text):
            log.info("COMMAND: close worst entry")
            await loop.run_in_executor(None, trader.close_worst_entry)
            return
        sig = parse_signal(text)
        if not sig:
            log.info("Not a valid gold signal, ignored: %r", text[:80])
            return
        log.info("SIGNAL: %s", sig)
        try:
            await loop.run_in_executor(None, trader.execute, sig)
        except Exception:
            log.exception("Execution failed")

    async def monitor():  # TP1 -> take partial profit + breakeven
        loop = asyncio.get_running_loop()
        while True:
            try:
                await loop.run_in_executor(None, trader.manage)
            except Exception:
                log.exception("Position management failed")
            await asyncio.sleep(2)

    client.loop.create_task(monitor())
    log.info("Listening to %s | dry_run=%s", channels, trader.dry)
    try:
        client.start()  # first run asks for phone number + login code
        client.run_until_disconnected()
    finally:
        trader.shutdown()


if __name__ == "__main__":
    main()
