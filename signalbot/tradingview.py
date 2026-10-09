from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone

import httpx

from .models import TvIdea

TV_LINK_RE = re.compile(r"https?://(?:[a-z]{2}\.|www\.)?tradingview\.com/(chart|x|i|script)/[^\s]+", re.I)
SNAPSHOT_RE = re.compile(r"tradingview\.com/x/([A-Za-z0-9]+)", re.I)
IDEA_ID_RE = re.compile(r"tradingview\.com/chart/[^/]+/([A-Za-z0-9]{8})-", re.I)
META_RE = re.compile(
    r"<meta[^>]+(?:property|name)=[\"']([^\"']+)[\"'][^>]*content=[\"']([^\"']*)[\"']", re.I
)
META_REV_RE = re.compile(
    r"<meta[^>]+content=[\"']([^\"']*)[\"'][^>]*(?:property|name)=[\"']([^\"']+)[\"']", re.I
)
JSONLD_RE = re.compile(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.I | re.S)
SYMBOL_RE = re.compile(r"/chart/([A-Z0-9._!]+)/", re.I)
# Idea pages embed their data as JSON inside scripts; the description sits in a string field.
JSON_TEXT_RE = re.compile(r'"(?:description|description_ast_text|text|articleBody)"\s*:\s*"((?:[^"\\]|\\.){40,})"')
JSON_DATE_RE = re.compile(r'"(?:created_at|published_at|datePublished|date)"\s*:\s*"(\d{4}-\d\d-\d\dT[^"]+)"')
JSON_STAMP_RE = re.compile(r'"(?:date_timestamp|created_timestamp)"\s*:\s*(\d{10})\b')
JSON_SIDE_RE = re.compile(r'"(?:direction|idea_direction|strategy)"\s*:\s*"?(long|short|1|2)"?', re.I)
TAG_STRIP_RE = re.compile(r"<(script|style|noscript)\b.*?</\1>|<[^>]+>", re.S | re.I)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


def find_tv_links(links: list[str]) -> list[str]:
    return [u for u in links if TV_LINK_RE.match(u)]


def snapshot_image_url(url: str) -> str | None:
    """Static chart picture for a snapshot link (/x/ID) or a published idea (/chart/SYMBOL/ID-slug)."""
    if m := SNAPSHOT_RE.search(url):
        sid = m.group(1)
        return f"https://s3.tradingview.com/snapshots/{sid[0].lower()}/{sid}.png"
    if m := IDEA_ID_RE.search(url):
        iid = m.group(1)
        return f"https://s3.tradingview.com/{iid[0].lower()}/{iid}_big.png"
    return None


def page_text(page: str) -> str:
    text = TAG_STRIP_RE.sub("\n", page)
    return re.sub(r"\n\s*\n+", "\n", html.unescape(text))


def _json_string(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw.replace("\\n", "\n")


def parse_idea_html(url: str, page: str) -> TvIdea:
    meta: dict[str, str] = {}
    for key, value in META_RE.findall(page):
        meta.setdefault(key.lower(), html.unescape(value))
    for value, key in META_REV_RE.findall(page):
        meta.setdefault(key.lower(), html.unescape(value))

    idea = TvIdea(
        url=url,
        title=meta.get("og:title", ""),
        description=meta.get("og:description", "") or meta.get("description", ""),
        image_url=meta.get("og:image") or snapshot_image_url(url),
    )
    if m := SYMBOL_RE.search(url):
        idea.symbol = m.group(1).upper()

    published = meta.get("article:published_time")
    for block in JSONLD_RE.findall(page):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if not isinstance(node, dict):
                continue
            published = published or node.get("datePublished") or node.get("uploadDate")
            body = node.get("articleBody") or node.get("text")
            # The full idea text is longer than og:description, prefer it.
            if isinstance(body, str) and len(body) > len(idea.description):
                idea.description = body

    # Meta descriptions are cut short. The complete text is in the embedded page data, in the
    # "ssrIdeaData" object; related ideas further down the page carry the same field names.
    at = page.find('"ssrIdeaData"')
    data = page[at:at + 30000] if at >= 0 else page
    for raw in JSON_TEXT_RE.findall(data):
        text = _json_string(raw)
        head = idea.description[:60].strip()
        if len(text) > len(idea.description) and (not head or head[:40] in text or not idea.description):
            idea.description = text

    # Last resort: the visible text that follows the title on the page.
    plain = page_text(page)
    title = idea.title.split(" by ")[0].strip() or idea.title
    at = plain.find(title[:50]) if title else -1
    if at >= 0:
        idea.page_excerpt = plain[at:at + 2500]

    if m := JSON_SIDE_RE.search(data):
        idea.side = {"1": "long", "2": "short"}.get(m.group(1), m.group(1).lower())

    if not published and (m := JSON_DATE_RE.search(data)):
        published = m.group(1)
    if published:
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
            idea.published = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    elif m := JSON_STAMP_RE.search(data):
        idea.published = datetime.fromtimestamp(int(m.group(1)), timezone.utc)
    return idea


class TradingView:
    def __init__(self, timeout: float = 8.0):
        self.http = httpx.AsyncClient(headers=HEADERS, timeout=timeout, follow_redirects=True)
        self.last_html = ""

    async def close(self) -> None:
        await self.http.aclose()

    async def fetch(self, url: str, with_image: bool = True) -> tuple[TvIdea, bytes | None]:
        """Return idea text plus chart image bytes. Never raises: a dead link yields an empty idea."""
        idea = TvIdea(url=url, image_url=snapshot_image_url(url))
        self.last_html = ""
        try:
            resp = await self.http.get(url.split("?")[0])
            idea.status = resp.status_code
            if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                self.last_html = resp.text
                idea = parse_idea_html(str(resp.url), resp.text)
                idea.status = 200
        except httpx.HTTPError as e:
            idea.status = -1
            idea.error = str(e)[:120]
        image = None
        if with_image and idea.image_url:
            try:
                img = await self.http.get(idea.image_url)
                if img.status_code == 200 and img.headers.get("content-type", "").startswith("image/"):
                    image = img.content
            except httpx.HTTPError:
                pass
        return idea, image
