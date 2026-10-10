# TradingView 12-hour catch-up for experimental LIVE

`TV_LIVE_ACK=I_ACCEPT_TWO_REAL_ORDERS` is a deliberate acknowledgement of two exchange requests per eligible trade, NOT a cap. The split remains 80%/20%.

## One-off run

Inside `railway ssh`, from `/app`:

```bash
python /app/tv_live_catchup.py --hours 12 --notify
```

This inspects **the most recent 12 hours** for all active, unsuspended authors. Uncached ideas use Gemini. Cached evidence is reused without extra Gemini usage. A cached idea is NOT blindly reopened: the live engine checks publication freshness (default 20 minutes from `config.toml`), author closure, current price, contract availability, Hedge Mode, existing positions/orders, and idempotency. It will not re-send previously attempted orders. A signal older than 20 minutes is SKIPPED even when the scan looks back 12 hours. Increase in scan horizon does not increase permissible trade age.

The normal watch loop stays running every 60 seconds. `TV_FULL_REVIEW_LOOKBACK_HOURS=12` can be used for a persistent 12-hour discovery horizon, but previously cached ideas are normally skipped; run this catch-up for cached rechecks.

## Required variables for live (user must explicitly configure)

```
TRADING_MODE=live
BINGX_LIVE_ACK=I_UNDERSTAND_REAL_ORDERS
TV_LIVE_EXECUTION=YES
TV_LIVE_ACK=I_ACCEPT_TWO_REAL_ORDERS
TG_LIVE_EXECUTION=NO
TV_FULL_REVIEW_ENABLED=1
TV_FULL_REVIEW_LOOKBACK_HOURS=12
```

**Experimental**: real exchange calls cannot be verified without the exchange account. Entry acknowledgement != filled order; attached exchange TP/SL protection and correct 80/20 behavior must be independently verified. Keep manual exchange access ready. If the second leg fails, manual intervention is required; the engine does not auto-compensate or repeat uncertain orders. Existing orders won't be automatically closed by disabling the watcher.

To prevent new entries, set `TV_LIVE_EXECUTION=NO` in Railway and redeploy, then inspect active positions/orders in BingX.
