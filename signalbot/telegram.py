from __future__ import annotations

import re
from datetime import timezone
from typing import Awaitable, Callable

from telethon import TelegramClient, events, utils
from telethon.errors import InviteRequestSentError, UserAlreadyParticipantError
from telethon.tl import types
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

from .models import Channel, TgMessage

URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']+", re.I)
INVITE_RE = re.compile(r"(?:t\.me|telegram\.me)/(?:\+|joinchat/)([\w-]+)", re.I)
PUBLIC_RE = re.compile(r"(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z]\w{3,})", re.I)
PRIVATE_ID_RE = re.compile(r"(?:t\.me|telegram\.me)/c/(\d+)", re.I)


def parse_channel_ref(ref: str) -> tuple[str, str | int]:
    """Classify a channel reference: ('invite', hash) | ('username', name) | ('id', peer_id)."""
    ref = ref.strip()
    if m := INVITE_RE.search(ref):
        return "invite", m.group(1)
    if m := PRIVATE_ID_RE.search(ref):
        return "id", int(f"-100{m.group(1)}")
    if m := PUBLIC_RE.search(ref):
        return "username", m.group(1)
    if ref.startswith("@"):
        return "username", ref[1:]
    if re.fullmatch(r"-?\d+", ref):
        return "id", int(ref)
    if re.fullmatch(r"[A-Za-z]\w{3,}", ref):
        return "username", ref
    raise ValueError(f"Not a Telegram channel link: {ref}")


def extract_links(text: str, entities: list | None) -> list[str]:
    links = [m.group(0).rstrip(".,;!?") for m in URL_RE.finditer(text or "")]
    for ent in entities or []:
        if isinstance(ent, types.MessageEntityTextUrl) and ent.url:
            links.append(ent.url)
    seen: set[str] = set()
    return [u for u in links if not (u in seen or seen.add(u))]


def to_message(msg, channel_id: int) -> TgMessage:
    fwd = msg.fwd_from
    fwd_from = None
    if fwd:
        fwd_from = fwd.from_name or fwd.post_author
        if not fwd_from and fwd.from_id is not None:
            fwd_from = str(utils.get_peer_id(fwd.from_id))
    links = extract_links(msg.message or "", msg.entities)
    markup = getattr(msg, "reply_markup", None)
    for row in getattr(markup, "rows", None) or []:
        for button in row.buttons:
            if getattr(button, "url", None):
                links.append(button.url)
    return TgMessage(
        channel_id=channel_id,
        message_id=msg.id,
        date=msg.date.astimezone(timezone.utc),
        text=msg.message or "",
        edit_date=msg.edit_date.astimezone(timezone.utc) if msg.edit_date else None,
        fwd_date=fwd.date.astimezone(timezone.utc) if fwd and fwd.date else None,
        fwd_from=fwd_from,
        reply_to=getattr(msg.reply_to, "reply_to_msg_id", None) if msg.reply_to else None,
        has_photo=bool(msg.photo),
        links=links,
    )


MessageHandler = Callable[[TgMessage, bool], Awaitable[None]]


def message_link(channel_id: int, username: str | None, message_id: int) -> str:
    if username:
        return f"https://t.me/{username}/{message_id}"
    internal = str(channel_id).removeprefix("-100").lstrip("-")
    return f"https://t.me/c/{internal}/{message_id}"


class Telegram:
    def __init__(self, api_id: int, api_hash: str, session: str):
        self.client = TelegramClient(session, api_id, api_hash)

    async def start(self) -> None:
        # Interactive on first run: asks for phone number and login code.
        await self.client.start()

    async def stop(self) -> None:
        await self.client.disconnect()

    async def resolve(self, ref: str, join: bool = True) -> Channel:
        """Resolve a link, join the channel if needed and return its identity."""
        kind, value = parse_channel_ref(ref)
        if kind == "invite":
            info = await self.client(CheckChatInviteRequest(value))
            if isinstance(info, types.ChatInviteAlready):
                entity = info.chat
            elif join:
                try:
                    await self.client(ImportChatInviteRequest(value))
                except UserAlreadyParticipantError:
                    pass
                except InviteRequestSentError:
                    raise ValueError("Канал приймає за заявкою: заявку надіслано, спробуйте після схвалення") from None
                # The shape of the join result differs between Telegram API layers, so the chat
                # is read back from the invite itself, which now reports the account as a member.
                info = await self.client(CheckChatInviteRequest(value))
                if not isinstance(info, types.ChatInviteAlready):
                    raise ValueError("Не вдалося вступити в канал за цим запрошенням")
                entity = info.chat
            else:
                raise ValueError("Private channel: joining is required to read it")
        else:
            entity = await self.client.get_entity(value)
            if join and isinstance(entity, types.Channel) and entity.left:
                try:
                    await self.client(JoinChannelRequest(entity))
                except UserAlreadyParticipantError:
                    pass
        return Channel(
            channel_id=utils.get_peer_id(entity),
            title=getattr(entity, "title", None) or str(value),
            username=getattr(entity, "username", None),
        )

    async def history(self, channel_id: int, limit: int) -> list[TgMessage]:
        out = []
        async for msg in self.client.iter_messages(channel_id, limit=limit):
            if isinstance(msg, types.Message):
                out.append(to_message(msg, channel_id))
        out.reverse()
        return out

    async def photo(self, channel_id: int, message_id: int) -> bytes | None:
        msg = await self.client.get_messages(channel_id, ids=message_id)
        if not msg or not msg.photo:
            return None
        return await self.client.download_media(msg, file=bytes, thumb=-1)

    async def me_id(self) -> int:
        return (await self.client.get_me()).id

    def listen(self, is_watched: Callable[[int], bool], on_message: MessageHandler) -> None:
        """Subscribe to all channels; `is_watched` is checked per message so channels can be
        added or switched off while the process runs."""

        def watched(event) -> bool:
            return is_watched(event.chat_id)

        @self.client.on(events.NewMessage(func=watched))
        async def _new(event):
            await on_message(to_message(event.message, event.chat_id), False)

        @self.client.on(events.MessageEdited(func=watched))
        async def _edit(event):
            await on_message(to_message(event.message, event.chat_id), True)

    async def run_forever(self) -> None:
        await self.client.catch_up()
        await self.client.run_until_disconnected()
