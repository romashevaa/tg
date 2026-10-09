from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class AppCfg:
    db_path: str = "signalbot.db"
    history_limit: int = 300
    context_messages: int = 8
    notify_ignored: bool = False
    check_messages: int = 10


@dataclass(frozen=True)
class AiCfg:
    provider: str = "gemini"
    parse_model: str = "gemini-3.8-flash"
    onboard_model: str = "gemini-3.8-flash"
    fallback_model: str = "gemini-3.5-flash-lite"
    requests_per_minute: float = 10
    min_confidence: float = 0.80
    min_confidence_image: float = 0.90


@dataclass(frozen=True)
class EvalCfg:
    history_messages: int = 1500
    chunk_messages: int = 400
    horizon_days: float = 5
    interval: str = "5m"
    fee_pct: float = 0.1
    min_trades: int = 20
    max_forward_lag_minutes: float = 20
    bare_calls_checked: int = 150
    tv_ideas_checked: int = 200
    image_posts_checked: int = 60
    images_auto: bool = True
    reveal_minutes: float = 5
    showcase_min_posts: int = 10


@dataclass(frozen=True)
class ValidatorCfg:
    max_age_minutes: float = 20
    entry_tolerance_pct: float = 0.4
    min_rr: float = 0.8
    min_sl_distance_pct: float = 0.3
    max_sl_distance_pct: float = 15.0
    dedup_hours: float = 48
    dedup_price_tolerance_pct: float = 0.5
    require_stop_loss: bool = True
    reject_forwards: bool = False


@dataclass(frozen=True)
class RiskCfg:
    default_market: str = "futures"
    risk_pct: float = 1.0
    fixed_margin_usdt: float = 20.0
    max_margin_pct: float = 10.0
    max_open_positions: int = 5
    default_leverage: int = 5
    max_leverage: int = 10
    use_signal_leverage: bool = True
    margin_type: str = "ISOLATED"
    take_profit: str = "first"
    limit_when_price_ran: bool = True
    limit_expiry_hours: float = 24
    spot_slippage_pct: float = 0.5


@dataclass(frozen=True)
class Secrets:
    tg_api_id: int = 0
    tg_api_hash: str = ""
    tg_session: str = "signalbot"
    bot_token: str = ""
    owner_id: int = 0
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    bingx_api_key: str = ""
    bingx_secret_key: str = ""


@dataclass(frozen=True)
class Config:
    app: AppCfg = field(default_factory=AppCfg)
    ai: AiCfg = field(default_factory=AiCfg)
    eval: EvalCfg = field(default_factory=EvalCfg)
    validator: ValidatorCfg = field(default_factory=ValidatorCfg)
    risk: RiskCfg = field(default_factory=RiskCfg)
    secrets: Secrets = field(default_factory=Secrets)
    trading_mode: str = "shadow"
    channel_overrides: dict = field(default_factory=dict)

    def for_channel(self, channel_id: int, username: str | None) -> "Config":
        """Return a copy with per-channel [channels."..."] overrides applied."""
        keys = [str(channel_id)]
        if username:
            keys += [f"@{username}", username]
        cfg = self
        for key in keys:
            over = self.channel_overrides.get(key)
            if not over:
                continue
            cfg = replace(
                cfg,
                validator=_merge(cfg.validator, over.get("validator", {})),
                risk=_merge(cfg.risk, over.get("risk", {})),
                ai=_merge(cfg.ai, over.get("ai", {})),
            )
        return cfg


def _merge(section, values: dict):
    known = {f.name for f in fields(section)}
    unknown = set(values) - known
    if unknown:
        raise ValueError(f"Unknown config keys in {type(section).__name__}: {sorted(unknown)}")
    return replace(section, **values)


def load_config(path: str | Path = "config.toml") -> Config:
    load_dotenv()
    raw: dict = {}
    p = Path(path)
    if p.exists():
        raw = tomllib.loads(p.read_text(encoding="utf-8"))

    mode = os.getenv("TRADING_MODE", "shadow").strip().lower()
    if mode not in ("shadow", "demo", "live"):
        raise ValueError("TRADING_MODE must be shadow, demo or live")

    risk = _merge(RiskCfg(), raw.get("risk", {}))
    if risk.take_profit not in ("first", "middle", "last"):
        raise ValueError("risk.take_profit must be first, middle or last")
    if risk.default_market not in ("futures", "spot"):
        raise ValueError("risk.default_market must be futures or spot")

    ai = _merge(AiCfg(), raw.get("ai", {}))
    if ai.provider not in ("gemini", "claude"):
        raise ValueError("ai.provider must be gemini or claude")

    return Config(
        app=_merge(AppCfg(), {**raw.get("app", {}), **({"db_path": os.environ["SIGNALBOT_DB_PATH"]} if os.getenv("SIGNALBOT_DB_PATH") else {})}),
        ai=ai,
        eval=_merge(EvalCfg(), raw.get("eval", {})),
        validator=_merge(ValidatorCfg(), raw.get("validator", {})),
        risk=risk,
        secrets=Secrets(
            tg_api_id=int(os.getenv("TG_API_ID") or 0),
            tg_api_hash=os.getenv("TG_API_HASH", ""),
            tg_session=os.getenv("TG_SESSION", "signalbot"),
            bot_token=os.getenv("BOT_TOKEN", ""),
            owner_id=int(os.getenv("OWNER_ID") or 0),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            bingx_api_key=os.getenv("BINGX_API_KEY", ""),
            bingx_secret_key=os.getenv("BINGX_SECRET_KEY", ""),
        ),
        trading_mode=mode,
        channel_overrides=raw.get("channels", {}),
    )
