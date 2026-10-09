from __future__ import annotations

import asyncio
import base64
import logging
import time
from datetime import datetime

from . import prompts
from .config import Config
from .models import Channel, ChannelProfile, Extraction, HistSignal, ParsedSignal, TgMessage, TvIdea

log = logging.getLogger("signalbot.ai")

MAX_TEXT = 1500


def render_message(m: TgMessage, max_text: int = MAX_TEXT) -> str:
    flags = [f"id={m.message_id}", f"date={m.date:%Y-%m-%d %H:%M}"]
    if m.fwd_date:
        flags.append(f"fwd(original={m.fwd_date:%Y-%m-%d %H:%M}, from={m.fwd_from or 'hidden'})")
    if m.reply_to:
        flags.append(f"reply_to={m.reply_to}")
    if m.edit_date:
        flags.append("edited")
    if m.has_photo:
        flags.append("photo")
    if m.links:
        flags.append("links=" + " ".join(m.links[:4]))
    text = m.text if len(m.text) <= max_text else m.text[:max_text] + " [cut]"
    return f"[{' | '.join(flags)}]\n{text or '(no text)'}"


def image_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"GIF":
        return "image/gif"
    return "image/jpeg"


def profile_context(channel: Channel) -> str:
    profile = channel.profile.model_dump_json(indent=1) if channel.profile else "(no profile yet)"
    return prompts.parse_context(profile, channel.title)


def onboard_text(channel: Channel, messages: list[TgMessage]) -> str:
    rendered = "\n\n".join(render_message(m, 900) for m in messages)
    return prompts.onboard_user(channel.title, rendered)


def extract_text(channel: Channel, messages: list[TgMessage]) -> str:
    profile = channel.profile.model_dump_json(indent=1) if channel.profile else "(no profile)"
    rendered = "\n\n".join(render_message(m, 1200) for m in messages)
    return prompts.extract_user(channel.title, profile, rendered)


def parse_text(msg: TgMessage, context: list[TgMessage], open_signals: list[str], ideas: list[TvIdea],
               image_count: int, now: datetime) -> str:
    parts = [f"Current time (UTC): {now:%Y-%m-%d %H:%M}"]
    if context:
        parts.append(
            "<previous_messages>\n" + "\n\n".join(render_message(m, 500) for m in context)
            + "\n</previous_messages>"
        )
    if open_signals:
        parts.append("<open_positions_from_this_channel>\n" + "\n".join(open_signals)
                     + "\n</open_positions_from_this_channel>")
    for idea in ideas:
        published = f"{idea.published:%Y-%m-%d %H:%M} UTC" if idea.published else "unknown"
        parts.append(
            f"<tradingview_idea url=\"{idea.url}\" symbol=\"{idea.symbol}\" published=\"{published}\">\n"
            f"{idea.title}\n{idea.description[:3000]}\n</tradingview_idea>"
        )
    parts.append(f"<new_message>\n{render_message(msg)}\n</new_message>")
    if image_count:
        parts.append(f"{image_count} image(s) from the message or its TradingView link are attached.")
    return "\n\n".join(parts)


class RateLimiter:
    """Spaces requests evenly so a burst of channel posts does not hit the per-minute quota."""

    def __init__(self, per_minute: float):
        self.interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if not self.interval:
            return
        async with self._lock:
            delay = self._next - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = max(time.monotonic(), self._next) + self.interval


class ClaudeAI:
    def __init__(self, api_key: str, parse_model: str, onboard_model: str):
        from anthropic import AsyncAnthropic

        self.client = AsyncAnthropic(api_key=api_key, timeout=30.0, max_retries=1)
        self.parse_model = parse_model
        self.onboard_model = onboard_model

    async def onboard(self, channel: Channel, messages: list[TgMessage]) -> ChannelProfile:
        resp = await self.client.messages.parse(
            model=self.onboard_model,
            max_tokens=8000,
            system=prompts.ONBOARD_SYSTEM,
            messages=[{"role": "user", "content": onboard_text(channel, messages)}],
            output_format=ChannelProfile,
            timeout=180.0,
        )
        return resp.parsed_output

    async def extract(self, channel: Channel, messages: list[TgMessage]) -> list[HistSignal]:
        resp = await self.client.messages.parse(
            model=self.parse_model,
            max_tokens=16000,
            system=prompts.EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": extract_text(channel, messages)}],
            output_format=Extraction,
            timeout=180.0,
        )
        return resp.parsed_output.signals

    async def parse(self, channel: Channel, msg: TgMessage, context: list[TgMessage], open_signals: list[str],
                    ideas: list[TvIdea], images: list[bytes], now: datetime) -> ParsedSignal:
        images = images[:3]
        # Instructions and the channel profile repeat for every message of a channel, so they are cached.
        system = [
            {"type": "text", "text": prompts.PARSE_SYSTEM},
            {"type": "text", "text": profile_context(channel), "cache_control": {"type": "ephemeral"}},
        ]
        content: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image_type(i),
                    "data": base64.standard_b64encode(i).decode(),
                },
            }
            for i in images
        ]
        content.append({"type": "text", "text": parse_text(msg, context, open_signals, ideas, len(images), now)})
        resp = await self.client.messages.parse(
            model=self.parse_model,
            max_tokens=1200,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=ParsedSignal,
        )
        return resp.parsed_output


class GeminiAI:
    # Pause before each attempt. Attempts alternate between the main and the fallback model,
    # because an overloaded or over-quota model usually stays that way for a while.
    PAUSES = (0.0, 0.5, 2.0, 4.0)
    # History work is not urgent: wait out per-minute quotas instead of failing.
    SLOW_PAUSES = (0.0, 5.0, 30.0, 65.0)
    PARSE_TIMEOUT = 25.0
    ONBOARD_TIMEOUT = 180.0

    def __init__(self, api_key: str, parse_model: str, onboard_model: str, fallback_model: str = "",
                 requests_per_minute: float = 10):
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.parse_model = parse_model
        self.onboard_model = onboard_model
        self.fallback_model = fallback_model
        self.limiter = RateLimiter(requests_per_minute)

    async def _generate(self, model: str, system: str, parts: list, schema, max_tokens: int, timeout: float,
                        pauses: tuple | None = None):
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema.model_json_schema(),
            thinking_config=types.ThinkingConfig(thinking_level="low"),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            max_output_tokens=max_tokens,
            temperature=0,
        )
        last: Exception | None = None
        for i, pause in enumerate(pauses or self.PAUSES):
            attempt_model = self.fallback_model if (i % 2 and self.fallback_model) else model
            if pause:
                await asyncio.sleep(pause)
            await self.limiter.wait()
            try:
                resp = await asyncio.wait_for(
                    self.client.aio.models.generate_content(model=attempt_model, contents=parts, config=config),
                    timeout,
                )
                return schema.model_validate_json(resp.text)
            except asyncio.TimeoutError:
                last = TimeoutError(f"Gemini {attempt_model}: no answer in {timeout:.0f} s")
                log.warning("Gemini timeout on %s", attempt_model)
            except errors.APIError as e:
                if e.code not in (429, 500, 503):
                    raise
                last = e
                log.warning("Gemini %s on %s", e.code, attempt_model)
        raise last  # type: ignore[misc]

    async def onboard(self, channel: Channel, messages: list[TgMessage]) -> ChannelProfile:
        return await self._generate(
            self.onboard_model, prompts.ONBOARD_SYSTEM, [onboard_text(channel, messages)], ChannelProfile,
            8000, self.ONBOARD_TIMEOUT, self.SLOW_PAUSES,
        )

    async def extract(self, channel: Channel, messages: list[TgMessage]) -> list[HistSignal]:
        result = await self._generate(
            self.parse_model, prompts.EXTRACT_SYSTEM, [extract_text(channel, messages)], Extraction,
            16000, self.ONBOARD_TIMEOUT, self.SLOW_PAUSES,
        )
        return result.signals

    async def parse(self, channel: Channel, msg: TgMessage, context: list[TgMessage], open_signals: list[str],
                    ideas: list[TvIdea], images: list[bytes], now: datetime) -> ParsedSignal:
        from google.genai import types

        images = images[:3]
        parts: list = [types.Part.from_bytes(data=i, mime_type=image_type(i)) for i in images]
        parts.append(parse_text(msg, context, open_signals, ideas, len(images), now))
        system = prompts.PARSE_SYSTEM + "\n" + profile_context(channel)
        return await self._generate(self.parse_model, system, parts, ParsedSignal, 2000, self.PARSE_TIMEOUT)


AI = ClaudeAI


def make_ai(cfg: Config):
    a, s = cfg.ai, cfg.secrets
    if a.provider == "gemini":
        return GeminiAI(s.gemini_api_key, a.parse_model, a.onboard_model, a.fallback_model, a.requests_per_minute)
    return ClaudeAI(s.anthropic_api_key, a.parse_model, a.onboard_model)
