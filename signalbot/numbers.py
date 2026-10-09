from __future__ import annotations

import re

TOKEN_RE = re.compile(r"(?<![\w.,])(\d[\d.,]*\d|\d)\s?([kKкК])?(?![\w])")
SPACED_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?)(?![\d])")


def _interpretations(token: str) -> set[float]:
    """All plausible values of a numeric token written with dots and/or commas."""
    token = token.strip(".,")
    out: set[float] = set()

    def add(s: str) -> None:
        try:
            out.add(float(s))
        except ValueError:
            pass

    dots, commas = token.count("."), token.count(",")
    if not dots and not commas:
        add(token)
    elif dots and commas:
        # The right-most separator is the decimal one.
        if token.rfind(".") > token.rfind(","):
            add(token.replace(",", ""))
        else:
            add(token.replace(".", "").replace(",", "."))
    else:
        sep = "." if dots else ","
        count = dots or commas
        tail = token.rsplit(sep, 1)[1]
        if count == 1:
            add(token.replace(sep, "."))
            if len(tail) == 3:
                add(token.replace(sep, ""))
        else:
            add(token.replace(sep, ""))
    return out


def numbers_in_text(text: str) -> set[float]:
    found: set[float] = set()
    for m in SPACED_RE.finditer(text):
        found |= _interpretations(re.sub(r"[   ]", "", m.group(1)))
    for m in TOKEN_RE.finditer(text):
        values = _interpretations(m.group(1))
        found |= values
        if m.group(2):
            found |= {v * 1000 for v in values}
    return found


def is_grounded(value: float, available: set[float]) -> bool:
    return any(abs(value - a) <= 1e-9 * max(1.0, abs(value)) for a in available)


def ungrounded(values: list[float], text: str) -> list[float]:
    """Values the model returned that do not literally appear in the source text."""
    available = numbers_in_text(text)
    return [v for v in values if not is_grounded(v, available)]
