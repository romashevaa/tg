from __future__ import annotations

import re

from .models import HistSignal

# Many channels post calls from a fixed template. Those are read here by rules: instant, free and
# repeatable. Anything this module cannot read with certainty is left to the model.

QUOTES = "USDT|USDC|USD"
# "#C/USDT" and "1000PEPE/USDT" are valid; without a separator the ticker needs two characters.
COIN_RE = re.compile(
    rf"(?<![A-Za-z0-9])(?:([A-Z0-9]{{1,15}})\s?[/\-_]\s?({QUOTES})|([A-Z0-9]{{2,15}}?)({QUOTES}))\b"
)
# "$DODOX" or "#SAGA": a tagged ticker with no quote currency.
TAG_RE = re.compile(r"[$#]([A-Z][A-Z0-9]{1,11})\b(?![/\-_]?[A-Za-z])")
# "LONG — DOGE" or "DOGE LONG": an untagged ticker next to the direction word.
HEAD_RES = (
    re.compile(r"\b(?i:long|short|buy|sell)\b\W{1,4}([A-Z][A-Z0-9]{1,9})\b"),
    re.compile(r"\b([A-Z][A-Z0-9]{1,9})\W{1,4}(?i:long|short)\b"),
)
NOT_TICKERS = {
    "SETUP", "NOW", "HERE", "ZONE", "ZON", "LIMIT", "STOP", "MARKET", "POSITION", "TRADE", "ENTRY", "CALL",
    "SCALP", "SWING", "TARGET", "TARGETS", "LEVERAGE", "CROSS", "THE", "AND", "FOR", "ALERT", "NEW", "TYPE",
    "VIP", "FREE", "SIGNAL", "SIGNALS", "LONG", "SHORT", "BUY", "SELL", "FUTURES", "SPOT", "TP", "SL",
    "USDT", "USDC", "USD", "CRYPTO", "ALTS", "DYOR", "NFA", "UPDATE", "ID", "TA", "ATH", "FOMC", "CPI",
}
FIAT = {"EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY", "XAU", "XAG", "CNY", "BHD"}
FOREX_RE = re.compile(
    r"(?<![A-Za-z])(?:EUR|GBP|AUD|NZD|USD|CAD|CHF|JPY|XAU|XAG)[\s/\-]?(?:USD|JPY|CAD|CHF|GBP|EUR|NZD|AUD)\b"
    r"|(?i:\b(gold|silver|crude|oil|us30|nas100|dxy|otc)\b)"
)

# "short-term" and "long term" describe a time frame, not a direction.
LONG_RE = re.compile(r"(?i)\b(long(?![\s\-]?term)|longing|buy|buying)\b|лонг|длинн|покуп|купів")
SHORT_RE = re.compile(r"(?i)\b(short(?![\s\-]?term)|shorting|sell|selling)\b|шорт|коротк|продаж")
URL_RE = re.compile(r"https?://\S+")
SIDE_LINE_RE = re.compile(r"(?im)^\W*(?:direction|position|type|my opinion|side|направление\s*позиции)\s*[:\-–]\s*(.*)$")

LABELS = {
    "entry": r"entry(?:\s*(?:zone|price|point|targets?|market))?|entries|enter|(?:buy|sell)\s*zone"
             r"|(?:buy|sell|long|short)(?=\s*[:：])|точк[аи]\s*входа|вход|вхід|точк[аи]\s*входу",
    "target": r"take[\s\-]?profits?|targets?|tp|цел[ьи]|ціл[ьі]|тейк[\s\-]?профит\w*|тейк\w*",
    "stop": r"stop[\s\-]?loss|stop|sl|st(?=\s*[:：])|стоп[\s\-]?лосс?|стоп",
    "leverage": r"leverage|lev|кредитное\s*плечо|плечо|плече",
}
LABEL_RE = re.compile(
    r"(?i)(?<![\w])(?:" + "|".join(f"(?P<{k}>{v})" for k, v in LABELS.items()) + r")"
    # An index such as "Target 1:" or "TP2" belongs to the label, a bare number after it does not.
    r"(?:\d{1,2}(?=\s*[:：\)\-–—]|\s)|\s+\d{1,2}(?=\s*[:：\)]))?\s*[:：\-–—]?",
)
ENUM_RE = re.compile(r"(?m)^\W*\d{1,2}\s*[\)\.]\s+(?=[^\w\n]*[$\d])")
# A price: digits with separators, optional k/K for thousands, never a percentage.
NUM_RE = re.compile(r"(?<![\w.,])\$?(\d+(?:[.,]\d+)*)\s?([kKкК])?\$?(?![\w%])(?!\s?%)(?![.,]\d)")
LEV_RE = re.compile(r"(?i)(?<![\w.])[xх]\s?(\d{1,3})\b|\b(\d{1,3})(?:\.\d+)?\s?[xх]\b")
# "(2-5x)", "20X", "x10-25": a leverage range, not a price.
HEAD_LEVERAGE_RE = re.compile(r"(?i)\b\d{1,3}(?:\.\d+)?\s?[-–]\s?\d{1,3}\s?[xх]\b|\b\d{1,3}\s?[xх]\b|[xх]\s?\d{1,3}\b")
PROSE_BEFORE_NUMBER_RE = re.compile(r"^[^\d$]*[^\W\d_]{3,}[^\d$]*[\d$]")
# Words that may stand between a label and its price: "SL: below 0.0677", "Stoploss at : 0.008".
FILLER_RE = re.compile(r"(?i)\b(below|above|under|over|near|around|close|price|market|limit|zone|ниже|выше)\b")

# Marks of a report about a past trade, or of a teaser with hidden levels.
RESULT_RE = re.compile(
    r"🔐|\d\s*✅|✅\s*$|(?i:\b(reached|achieved|hit|closed)\b)|(?i:\d\s*%\s*profit)|(?i:profit\s*:\s*\+\d)"
    r"|(?i:(?<!take\s)(?<!take-)profit\s*:\s*[\d.,]+\s*%)|(?i:достигнут|прибыль|закрыт|result)"
    r"|[-–:]\s*\*\*\s*$",
    re.M,
)
GAIN_RE = re.compile(r"(?<![\w.])\+\s?\d[\d.,]*\s?%")


def to_number(token: str) -> float | None:
    """One reading per token: a dot is a decimal point, a comma before 3 digits groups thousands."""
    token = token.strip(".,")
    if "," in token and "." in token:
        if token.rfind(".") > token.rfind(","):
            token = token.replace(",", "")
        else:
            token = token.replace(".", "").replace(",", ".")
    elif "," in token:
        head, _, tail = token.rpartition(",")
        token = token.replace(",", "") if (len(tail) == 3 and head.strip("0")) else token.replace(",", ".")
    elif token.count(".") > 1:
        token = token.replace(".", "")
    try:
        value = float(token)
    except ValueError:
        return None
    return value if value > 0 else None


def numbers(segment: str) -> list[float]:
    segment = ENUM_RE.sub("", segment)
    out = []
    for m in NUM_RE.finditer(segment):
        value = to_number(m.group(1))
        if value is not None:
            out.append(value * 1000 if m.group(2) else value)
    return out


def split_fields(text: str) -> dict[str, list[str]]:
    """Cut the text at field labels; each label owns the text up to the next label."""
    marks = [(m.start(), m.end(), m.lastgroup) for m in LABEL_RE.finditer(text)]
    fields: dict[str, list[str]] = {}
    for i, (_, end, kind) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        fields.setdefault(kind, []).append(text[end:stop])
    return fields


def find_coin(text: str) -> tuple[str, str] | None:
    """(base, quote) when the message names exactly one instrument."""
    pairs = {(m.group(1) or m.group(3), m.group(2) or m.group(4)) for m in COIN_RE.finditer(text)}
    pairs = {(b, q) for b, q in pairs if b not in NOT_TICKERS}
    bases = {b for b, _ in pairs}
    if len(bases) == 1:
        base = bases.pop()
        quotes = {q for b, q in pairs if b == base}
        return base, "USDT" if "USDT" in quotes else sorted(quotes)[0]
    if bases or FOREX_RE.search(text):
        return None
    tags = {m.group(1) for m in TAG_RE.finditer(text)} - NOT_TICKERS
    if len(tags) == 1:
        return tags.pop(), "USDT"
    if tags:
        return None
    heads = {m.group(1) for rx in HEAD_RES for m in rx.finditer(text)} - NOT_TICKERS
    if len(heads) == 1:
        return heads.pop(), "USDT"
    if heads:
        return None
    # "SOL Next Target 150$" / "SOL - Solana": the ticker opens the message.
    first = re.match(r"\W*([A-Z][A-Z0-9]{1,9})\b(?![a-z])", text)
    return (first.group(1), "USDT") if first and first.group(1) not in NOT_TICKERS else None


def labelled_side(text: str) -> str | None:
    """Direction stated on its own line: "Direction: LONG", "My opinion: Sell"."""
    for m in SIDE_LINE_RE.finditer(text):
        long, short = LONG_RE.search(m.group(1)), SHORT_RE.search(m.group(1))
        if bool(long) != bool(short):
            return "long" if long else "short"
    return None


def side_from_levels(entries: list[float], stop: float, targets: list[float]) -> str | None:
    """Direction implied by where the stop and the targets sit around the entry."""
    if not entries or not targets:
        return None
    low, high = min(entries), max(entries)
    if stop < low and all(t > high for t in targets):
        return "long"
    if stop > high and all(t < low for t in targets):
        return "short"
    return None


def find_side(text: str) -> str | None:
    """Direction of the call. A labelled line ("Direction: LONG") outranks words in the prose."""
    for scope in [m.group(1) for m in SIDE_LINE_RE.finditer(text)] + [text]:
        long, short = LONG_RE.search(scope), SHORT_RE.search(scope)
        if bool(long) != bool(short):
            return "long" if long else "short"
        if long and short and scope is not text:
            return None
    # Both words appear in the prose: trust the headline only.
    head = "\n".join(line for line in text.split("\n") if line.strip())[:80]
    long, short = LONG_RE.search(head), SHORT_RE.search(head)
    if bool(long) != bool(short):
        return "long" if long else "short"
    return None


def find_leverage(text: str, fields: dict[str, list[str]]) -> int | None:
    for seg in fields.get("leverage", []):
        m = LEV_RE.search(seg[:40]) or re.search(r"(\d{1,3})", seg[:40])
        if m:
            return int(next(g for g in m.groups() if g))
    m = re.search(r"(?i)(?:cross|isolated)\D{0,6}(\d{1,3})(?:\.\d+)?\s?[xх]", text)
    if m is None:
        # "(2-5x)" next to the pair: take the upper end of the stated range.
        m = re.search(r"\(\s?\d{1,3}\s?[-–]\s?(\d{1,3})\s?[xXхХ]\s?\)", text)
    if m is None:
        # "AEVO LONG 20x": leverage written on the headline next to the direction.
        m = re.search(r"(?i)\b(?:long|short)\b\W{0,3}(\d{1,3})\s?[xх]\b", text)
    return int(m.group(1)) if m else None


def looks_like_call(text: str) -> bool:
    """A message worth showing to the model: it carries trade levels and is not a report."""
    text = URL_RE.sub(" ", text)
    coin = find_coin(text)
    if RESULT_RE.search(text) or (FOREX_RE.search(text) and (coin is None or coin[0] in FIAT)):
        return False
    fields = split_fields(text)

    def has(kind: str) -> bool:
        return any(numbers(seg[:80]) for seg in fields.get(kind, []))

    return has("stop") and has("target") and (find_side(text) is not None or has("entry"))


def bare_call(text: str) -> tuple[str, str] | None:
    """A short "Buying $X here" style post with no levels: returns (base, side) or None."""
    text = URL_RE.sub(" ", text).strip()
    if len(text) > 160 or RESULT_RE.search(text) or GAIN_RE.search(text):
        return None
    coin, side = find_coin(text), find_side(text)
    if coin is None or side is None or coin[0] in FIAT or FOREX_RE.search(text):
        return None
    fields = split_fields(text)
    if any(numbers(seg[:80]) for kind in ("stop", "target") for seg in fields.get(kind, [])):
        return None
    return coin[0], side


def parse_call(text: str, message_id: int = 0) -> HistSignal | None:
    """Read a template call. Returns None unless coin, side, stop and targets are all unambiguous."""
    # Link slugs carry words and numbers of their own ("…/BTCUSDT-Short-term-…").
    text = URL_RE.sub(" ", text)
    if RESULT_RE.search(text):
        return None
    coin = find_coin(text)
    if coin is None or coin[0] in FIAT:
        return None

    # A price on the headline next to the coin or the direction ("BTC LONG 64000-64500") is an
    # entry written without a label. Rules do not guess what it is; the model reads such posts.
    first = LABEL_RE.search(text)
    for line in (text[:first.start()] if first else text).split("\n"):
        named = COIN_RE.search(line) or TAG_RE.search(line) or LONG_RE.search(line) or SHORT_RE.search(line)
        if named and numbers(HEAD_LEVERAGE_RE.sub(" ", line)):
            return None

    fields = split_fields(text)
    stops = [n for seg in fields.get("stop", []) for n in numbers(_block(seg.split("\n\n")[0]))]
    targets = [n for seg in fields.get("target", []) for n in numbers(_block(seg))]
    entries = [n for seg in fields.get("entry", []) for n in numbers(_block(seg))]
    entries = list(dict.fromkeys(entries))
    if len(stops) != 1 or not targets or len(entries) > 4:
        return None
    stop = stops[0]

    # With an entry price the geometry of the levels gives the direction. A direction stated on
    # its own line must agree with it; loose words in the commentary ("short squeeze") need not.
    implied = side_from_levels(entries, stop, targets)
    stated = labelled_side(text)
    if implied and stated and implied != stated:
        return None
    side = stated or implied or find_side(text)
    if side is None:
        return None
    targets = _order_targets(targets, side)

    low, high = (min(entries), max(entries)) if entries else (None, None)
    if side == "long":
        ok = all(tp > stop for tp in targets) and (not entries or (stop < low and all(tp > high for tp in targets)))
    else:
        ok = all(tp < stop for tp in targets) and (not entries or (stop > high and all(tp < low for tp in targets)))
    if not ok:
        return None
    return HistSignal(
        message_id=message_id,
        base_asset=coin[0],
        quote_asset=coin[1],
        side=side,
        entries=entries,
        stop_loss=stop,
        take_profits=targets,
        leverage=find_leverage(text, fields),
    )


def _order_targets(targets: list[float], side: str) -> list[float]:
    """Nearest target first. A headline figure far beyond the rest ("Next target 90k") is dropped."""
    unique = sorted(set(targets), reverse=side == "short")
    if len(unique) >= 3:
        mid = sorted(unique)[len(unique) // 2]
        unique = [t for t in unique if mid / 3 <= t <= mid * 3]
    return unique


def _block(segment: str) -> str:
    """The run of price lines that follows a label. Stops at the first line of prose after it."""
    out, seen = [], False
    for i, line in enumerate(segment.split("\n")):
        has_number = bool(NUM_RE.search(ENUM_RE.sub("", line + " ")))
        if has_number:
            # A sentence between the label and a number means the number is not the labelled price.
            if PROSE_BEFORE_NUMBER_RE.match(FILLER_RE.sub("", line)):
                break
            seen = True
            out.append(line)
        elif seen and line.strip():
            break
        elif not seen:
            out.append(line)
    return "\n".join(out)
