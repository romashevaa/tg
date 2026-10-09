from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .ai import make_ai
from .bingx import BingX
from .bot import Bot
from .config import Config, load_config
from .executor import Executor
from .pipeline import Pipeline
from .prices import PriceHistory
from .storage import Storage
from .telegram import Telegram
from .tradingview import TradingView


def check_env(cfg: Config) -> None:
    s = cfg.secrets
    missing = []
    if not (s.tg_api_id and s.tg_api_hash):
        missing.append("TG_API_ID, TG_API_HASH")
    if not s.bot_token:
        missing.append("BOT_TOKEN")
    if cfg.ai.provider == "gemini" and not s.gemini_api_key:
        missing.append("GEMINI_API_KEY")
    if cfg.ai.provider == "claude" and not s.anthropic_api_key:
        missing.append("ANTHROPIC_API_KEY")
    if cfg.trading_mode != "shadow" and not (s.bingx_api_key and s.bingx_secret_key):
        missing.append("BINGX_API_KEY, BINGX_SECRET_KEY (required for demo and live)")
    if missing:
        sys.exit("Missing in .env: " + "; ".join(missing))


async def cmd_login(cfg: Config) -> None:
    """One-time interactive login of the reader account; stores the session file."""
    tg = Telegram(cfg.secrets.tg_api_id, cfg.secrets.tg_api_hash, cfg.secrets.tg_session)
    await tg.start()
    me = await tg.client.get_me()
    print(f"Logged in as {me.first_name} (id {me.id}). Session saved.")
    await tg.stop()


async def cmd_run(cfg: Config) -> None:
    check_env(cfg)
    s = cfg.secrets
    storage = Storage(cfg.app.db_path)
    tg = Telegram(s.tg_api_id, s.tg_api_hash, s.tg_session)
    await tg.start()
    # Without OWNER_ID the bot answers only to the reader account itself.
    owner = s.owner_id or await tg.me_id()
    bot = Bot(s.tg_api_id, s.tg_api_hash, s.bot_token, owner)
    exchange = BingX(s.bingx_api_key, s.bingx_secret_key, demo=cfg.trading_mode == "demo")
    tv = TradingView()
    prices = PriceHistory(exchange)
    pipeline = Pipeline(cfg, storage, make_ai(cfg), exchange, Executor(exchange, cfg), tv, tg, bot, prices)
    await bot.start(pipeline)
    tg.listen(pipeline.is_watched, pipeline.handle)

    print(f"Mode: {cfg.trading_mode}. AI: {cfg.ai.provider}/{cfg.ai.parse_model}. "
          f"Channels: {len(pipeline.watched)}. Owner id: {owner}. Open the bot and send /start.")
    try:
        await bot.send(f"Бот запущено. Режим: {cfg.trading_mode}. Каналів: {len(pipeline.watched)}.\n/help — команди")
    except Exception:
        print("Could not message the owner yet: open the bot in Telegram and press Start.")
    try:
        await tg.run_forever()
    finally:
        await bot.stop()
        await exchange.close()
        await tv.close()
        await prices.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="signalbot")
    parser.add_argument("--config", default="config.toml")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="log the reader Telegram account in (once)")
    sub.add_parser("run", help="start the reader and the control bot")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("telethon", "httpx", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    cfg = load_config(args.config)
    asyncio.run(cmd_login(cfg) if args.cmd == "login" else cmd_run(cfg))
