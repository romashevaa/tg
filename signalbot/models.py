from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ProfileExample(BaseModel):
    message_id: int
    kind: str = Field(description="new_signal, update, result, demo_or_promo, analysis or noise")
    excerpt: str = Field(description="Short verbatim excerpt of the message")
    explanation: str = Field(description="How to read it: where side, entry, stop and targets are")


class ChannelProfile(BaseModel):
    """What the model learned about one channel from its history."""

    language: str
    summary: str = Field(description="What the channel posts and how often it posts real signals")
    signal_format: str = Field(description="How a tradable signal looks and where each field sits")
    terminology: list[str] = Field(description="Channel slang and abbreviations with meanings")
    update_patterns: str = Field(description="How follow-ups look: move SL, close, partial take, cancel")
    non_signal_patterns: str = Field(
        description="How results, VIP teasers, old forwarded calls, ads and plain analysis look"
    )
    default_market: Literal["futures", "spot", "unknown"]
    default_entry_type: Literal["market", "limit", "breakout", "unknown"]
    multi_message_signals: bool = Field(description="Signal data is split across several messages")
    levels_in_images: bool = Field(description="Trade levels are often only on pictures or charts")
    uses_tradingview: bool
    typical_leverage: int | None
    signals_found: int = Field(description="Number of real tradable signals in the sample")
    tradable: bool = Field(description="Signals are complete enough to trade automatically")
    examples: list[ProfileExample] = Field(description="3-6 representative messages of different kinds")
    notes: str


Kind = Literal["new_signal", "update", "result", "demo_or_promo", "analysis", "noise"]


class ParsedSignal(BaseModel):
    """Model output for one message. This is a reading of the message, not an order."""

    kind: Kind
    confidence: float = Field(description="0..1 confidence in kind and in every extracted number")
    base_asset: str = Field(description="Ticker of the coin without quote, e.g. BTC. Empty if none")
    quote_asset: str = Field(description="Quote currency, USDT unless stated otherwise")
    market: Literal["futures", "spot", "unknown"]
    side: Literal["long", "short", "none"]
    entry_type: Literal["market", "limit", "breakout", "none"]
    entry_low: float | None = Field(description="Lower bound of entry; equals entry_high for one price")
    entry_high: float | None
    stop_loss: float | None
    take_profits: list[float] = Field(description="Targets in the order the author lists them")
    leverage: int | None
    margin_type: Literal["ISOLATED", "CROSSED"] | None = Field(default=None, description="Explicit margin mode of the author only; null if absent or ambiguous")
    update_action: Literal["none", "close", "move_sl", "cancel", "partial_close", "other"]
    update_stop_loss: float | None
    refers_to_message_id: int | None = Field(description="Earlier channel message this one is about")
    numbers_from_image: bool = Field(description="Any level was read from an image, not from text")
    stale_hints: list[str] = Field(
        description="Evidence the call is old, already played out, a VIP repost or a showcase"
    )
    reason: str = Field(description="One sentence explaining the classification")


class HistSignal(BaseModel):
    """One past trade call found in channel history, used only for evaluating the channel."""

    message_id: int
    base_asset: str = Field(description="Bare ticker, e.g. BTC")
    quote_asset: str = Field(description="USDT unless stated otherwise")
    side: Literal["long", "short"]
    entries: list[float] = Field(description="Entry prices as written; empty for market entry")
    stop_loss: float | None
    take_profits: list[float] = Field(description="Targets in the order listed")
    leverage: int | None


class Extraction(BaseModel):
    signals: list[HistSignal]


@dataclass
class TgMessage:
    channel_id: int
    message_id: int
    date: datetime
    text: str = ""
    edit_date: datetime | None = None
    fwd_date: datetime | None = None
    fwd_from: str | None = None
    reply_to: int | None = None
    has_photo: bool = False
    links: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.channel_id}:{self.message_id}"


@dataclass
class TvIdea:
    url: str
    title: str = ""
    description: str = ""
    symbol: str = ""
    published: datetime | None = None
    image_url: str | None = None
    side: str = ""
    page_excerpt: str = ""
    status: int = 0
    error: str = ""

    def call_texts(self) -> list[str]:
        """Texts that may hold the trade levels, best first, with the idea's direction label added."""
        label = f"\nDirection: {self.side.upper()}" if self.side else ""
        symbol = re.sub(r"\.P$", "", self.symbol)
        head = f"{symbol}\n" if symbol else ""
        return [head + t + label for t in (self.description, self.page_excerpt) if t.strip()]


@dataclass
class Channel:
    channel_id: int
    title: str
    username: str | None
    profile: ChannelProfile | None = None
    enabled: bool = True
    evaluation: dict | None = None


@dataclass
class MarketInfo:
    """Exchange facts about one symbol, used by the validator and sizing."""

    symbol: str
    market: str
    price: float
    price_precision: int
    qty_precision: int
    min_qty: float
    min_notional: float
    max_leverage: int = 1


@dataclass
class OrderPlan:
    """Deterministic order built from a validated signal."""

    symbol: str
    market: str
    side: str
    order_type: str
    quantity: float
    entry_price: float
    limit_price: float | None
    stop_loss: float | None
    take_profit: float | None
    leverage: int
    margin_usdt: float
    risk_usdt: float
    client_id: str


@dataclass
class Decision:
    action: Literal["execute", "manual", "reject", "ignore"]
    reasons: list[str] = field(default_factory=list)
    plan: OrderPlan | None = None
