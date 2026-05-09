"""Long-lived Pyrogram MTProto client.

Mirrors the design of ``QuotexManager``: a single async-safe wrapper
that owns one Pyrogram ``Client`` for the whole app, exposes a tight
public surface, and serialises auth transitions through a lock so two
concurrent requests can't race into half-built state.

Login is a state machine because the auth flow has *three* possible
HTTP turns (phone -> code -> optional 2FA password):

    idle -> awaiting_code -> [awaiting_password] -> logged_in

The Pyrogram ``Client`` is created in-memory; once login succeeds we
export the session string, encrypt it with the app Fernet key, and
persist it to SQLite so subsequent container restarts skip the SMS
round-trip entirely.

Pyrogram's upstream releases have been quiet — if API drift becomes a
problem the import in this module is the *only* swap needed: drop in
``hydrogram`` or ``pyrofork`` (both API-compatible forks) without
touching application code.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import structlog

# isort: off
# ---------------------------------------------------------------------------
# Pyrogram 2.0.106 hard-codes a too-small MIN_CHANNEL_ID, so any modern
# 64-bit Telegram channel (anything past about ``-1_002_147_483_647``)
# raises ``ValueError: Peer id invalid`` deep inside ``handle_updates``,
# killing update dispatch *before* our MessageHandler runs. Maintained
# forks (hydrogram, pyrofork) widened the constants years ago — until
# we migrate, patch them here at import time so dispatch works for all
# in-the-wild channel IDs.
# ---------------------------------------------------------------------------
import pyrogram.utils as _pyro_utils

# 2**41 covers all currently-issued Telegram channel IDs with comfortable
# headroom; ``get_peer_type`` checks ``MIN_CHANNEL_ID <= peer_id < MAX_CHANNEL_ID``
# so we widen the lower bound only.
_pyro_utils.MIN_CHANNEL_ID = -(1 << 41) - 1_000_000_000_000  # ~ -3.2 * 10^12

from pyrogram import Client  # noqa: E402
from pyrogram.errors import (  # noqa: E402
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberInvalid,
    SessionPasswordNeeded,
)

from autotrader.config import telegram_settings  # noqa: E402

# isort: on

# A pipeline-style callback. The manager calls this for every incoming
# text/sticker message in any chat — the consumer (typically the
# Pipeline service) decides whether the chat is watched.
MessageCallback = Callable[["IncomingMessage"], Awaitable[None]]

log = structlog.get_logger(__name__)

LoginState = Literal[
    "idle",
    "awaiting_code",
    "awaiting_password",
    "logged_in",
    "error",
]


class TelegramManagerError(Exception):
    """Raised for caller-visible failures (bad code, missing API creds, …)."""


@dataclass(frozen=True, slots=True)
class TelegramStatus:
    """Public snapshot — safe to serialise to the dashboard."""

    state: LoginState
    logged_in: bool
    phone_masked: str | None
    user_id: int | None
    username: str | None
    first_name: str | None
    awaiting_code: bool
    awaiting_password: bool
    last_error: str | None


@dataclass(frozen=True, slots=True)
class Dialog:
    """Trimmed projection of ``pyrogram.types.Dialog`` for the UI."""

    chat_id: int
    title: str
    chat_type: str          # "channel" | "group" | "supergroup" | "private" | "bot"
    username: str | None
    members_count: int | None
    is_verified: bool


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    """Trimmed projection passed to the live message callback."""

    chat_id: int
    sender_id: int
    text: str
    media_kind: str         # "text" | "caption" | "sticker"
    received_at: datetime


def _mask_phone(phone: str) -> str:
    digits = phone.lstrip("+")
    if len(digits) <= 4:
        return "+" + "*" * len(digits)
    return f"+{digits[:2]}{'*' * (len(digits) - 4)}{digits[-2:]}"


class TelegramManager:
    """Single warm Pyrogram client, async-safe."""

    # Fixed Pyrogram session name; we use in-memory sessions so this
    # never lands on disk — the encrypted session string in the DB is
    # the source of truth.
    _CLIENT_NAME = "autotrader"

    def __init__(self) -> None:
        self._client: Client | None = None
        self._state: LoginState = "idle"
        self._phone: str | None = None
        self._phone_code_hash: str | None = None
        self._user_id: int | None = None
        self._username: str | None = None
        self._first_name: str | None = None
        self._last_error: str | None = None
        self._lock = asyncio.Lock()
        # Live message callback — set by the lifespan after the
        # pipeline is constructed. The manager calls this for every
        # incoming text/sticker message; ``None`` means "no consumer".
        self._on_message: MessageCallback | None = None
        self._handler_attached: bool = False
        self._prime_task: asyncio.Task[None] | None = None
        # Live-update health gauges. ``last_message_at`` ticks every
        # time ``_handle_incoming`` accepts a message — a stale value
        # means Telegram has gone quiet. ``subscribed_chat_count``
        # captures the number of WatchedChannels that the post-prime
        # touch loop successfully resolved (see ``_prime_peer_cache``).
        # Both surface in the pipeline-status payload so the dashboard
        # can show a "Last channel msg / Channels subscribed" gauge
        # without scraping logs.
        self._last_message_at: datetime | None = None
        self._subscribed_chat_count: int = 0

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    @property
    def logged_in(self) -> bool:
        return self._state == "logged_in" and self._client is not None

    @property
    def last_message_at(self) -> datetime | None:
        return self._last_message_at

    @property
    def subscribed_chat_count(self) -> int:
        return self._subscribed_chat_count

    def status(self) -> TelegramStatus:
        return TelegramStatus(
            state=self._state,
            logged_in=self.logged_in,
            phone_masked=_mask_phone(self._phone) if self._phone else None,
            user_id=self._user_id,
            username=self._username,
            first_name=self._first_name,
            awaiting_code=self._state == "awaiting_code",
            awaiting_password=self._state == "awaiting_password",
            last_error=self._last_error,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _api_credentials() -> tuple[int, str]:
        """Pull (api_id, api_hash) from env or raise a clear error."""
        if telegram_settings.api_id is None or telegram_settings.api_hash is None:
            raise TelegramManagerError(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH must be set "
                "(get them from https://my.telegram.org/apps)",
            )
        return telegram_settings.api_id, telegram_settings.api_hash.get_secret_value()

    def _new_client(self, *, session_string: str | None = None) -> Client:
        api_id, api_hash = self._api_credentials()
        return Client(
            self._CLIENT_NAME,
            api_id=api_id,
            api_hash=api_hash,
            session_string=session_string,
            in_memory=True,
            no_updates=False,
        )

    async def _capture_self(self) -> None:
        """Fill ``user_id``/``username``/``first_name`` from /getMe."""
        assert self._client is not None
        me = await self._client.get_me()
        self._user_id = me.id
        self._username = getattr(me, "username", None)
        self._first_name = getattr(me, "first_name", None)
        self._phone = self._phone or getattr(me, "phone_number", None)

    # ------------------------------------------------------------------
    # Session restore
    # ------------------------------------------------------------------

    async def restore(self, session_string: str, phone: str | None = None) -> bool:
        """Re-attach a previously-saved Pyrogram session.

        Returns True when the session is still valid, False otherwise
        (caller should wipe the DB row in that case).
        """
        async with self._lock:
            if self._client is not None:
                return self.logged_in
            try:
                client = self._new_client(session_string=session_string)
                await client.start()
                self._client = client
                self._phone = phone
                await self._capture_self()
                self._state = "logged_in"
                self._last_error = None
                self._attach_handler_if_pending()
                # Prime Pyrogram's peer cache so update dispatch can
                # resolve channel IDs without ``Peer id invalid``
                # errors when a message lands in an unseen chat.
                self._prime_task = asyncio.create_task(self._prime_peer_cache())
                log.info(
                    "telegram.restore.ok",
                    user_id=self._user_id,
                    username=self._username,
                )
                return True
            except Exception as exc:
                self._last_error = f"restore: {type(exc).__name__}: {exc}"
                log.warning("telegram.restore.failed", error=self._last_error)
                with contextlib.suppress(Exception):
                    if self._client is not None:
                        await self._client.stop()
                self._client = None
                self._state = "idle"
                return False

    # ------------------------------------------------------------------
    # Login flow
    # ------------------------------------------------------------------

    async def begin_login(self, phone: str) -> None:
        """Send an SMS / Telegram-app login code to ``phone``."""
        async with self._lock:
            if self._state in ("awaiting_code", "awaiting_password"):
                raise TelegramManagerError(
                    "a login is already in progress — submit the code or cancel",
                )
            if self.logged_in:
                raise TelegramManagerError("already logged in — log out first")

            await self._teardown_client()

            client = self._new_client()
            try:
                await client.connect()
                sent = await client.send_code(phone)
            except PhoneNumberInvalid as exc:
                self._state = "error"
                self._last_error = "invalid phone number"
                with contextlib.suppress(Exception):
                    await client.disconnect()
                raise TelegramManagerError(self._last_error) from exc
            except Exception as exc:
                self._state = "error"
                self._last_error = f"send_code: {type(exc).__name__}: {exc}"
                with contextlib.suppress(Exception):
                    await client.disconnect()
                log.exception("telegram.send_code.failed")
                raise TelegramManagerError(self._last_error) from exc

            self._client = client
            self._phone = phone
            self._phone_code_hash = sent.phone_code_hash
            self._state = "awaiting_code"
            self._last_error = None
            log.info("telegram.send_code.ok", phone_masked=_mask_phone(phone))

    async def submit_code(self, code: str) -> None:
        """Complete the SMS step. May transition to ``awaiting_password``.

        After this returns successfully the manager is either
        ``logged_in`` (no 2FA on the account) or ``awaiting_password``
        (2FA enabled — call :meth:`submit_password` next).
        """
        async with self._lock:
            if self._state != "awaiting_code":
                raise TelegramManagerError("not waiting for a code")
            assert self._client is not None
            assert self._phone is not None
            assert self._phone_code_hash is not None

            try:
                await self._client.sign_in(
                    self._phone,
                    self._phone_code_hash,
                    code,
                )
            except SessionPasswordNeeded:
                self._state = "awaiting_password"
                log.info("telegram.sign_in.password_needed")
                return
            except (PhoneCodeInvalid, PhoneCodeExpired) as exc:
                # Stay in awaiting_code so the user can retry without
                # re-sending an SMS — Pyrogram allows resubmission.
                self._last_error = (
                    "code expired — request a new one"
                    if isinstance(exc, PhoneCodeExpired)
                    else "invalid code"
                )
                raise TelegramManagerError(self._last_error) from exc
            except Exception as exc:
                self._state = "error"
                self._last_error = f"sign_in: {type(exc).__name__}: {exc}"
                log.exception("telegram.sign_in.failed")
                raise TelegramManagerError(self._last_error) from exc

            await self._finalise_login()

    async def submit_password(self, password: str) -> None:
        async with self._lock:
            if self._state != "awaiting_password":
                raise TelegramManagerError("not waiting for a 2FA password")
            assert self._client is not None

            try:
                await self._client.check_password(password)
            except PasswordHashInvalid as exc:
                self._last_error = "invalid 2FA password"
                raise TelegramManagerError(self._last_error) from exc
            except Exception as exc:
                self._state = "error"
                self._last_error = f"check_password: {type(exc).__name__}: {exc}"
                log.exception("telegram.check_password.failed")
                raise TelegramManagerError(self._last_error) from exc

            await self._finalise_login()

    async def _finalise_login(self) -> None:
        """Common tail for sign_in / check_password success."""
        assert self._client is not None
        # Pyrogram's connect() already brought the socket up; we now
        # need start() so the update dispatcher is ready for live
        # messages. ``start()`` on an already-connected client just
        # turns updates on.
        with contextlib.suppress(ConnectionError):
            await self._client.initialize()  # type: ignore[attr-defined]
        await self._capture_self()
        self._state = "logged_in"
        self._last_error = None
        self._phone_code_hash = None
        self._attach_handler_if_pending()
        # Prime Pyrogram's peer cache so update dispatch can resolve
        # channel IDs without ``Peer id invalid`` errors.
        self._prime_task = asyncio.create_task(self._prime_peer_cache())
        log.info(
            "telegram.login.ok",
            user_id=self._user_id,
            username=self._username,
        )

    async def export_session_string(self) -> str:
        """Caller is expected to encrypt + persist the result."""
        if not self.logged_in:
            raise TelegramManagerError("not logged in")
        assert self._client is not None
        return await self._client.export_session_string()

    async def cancel_login(self) -> None:
        """Tear down an in-flight login (idempotent)."""
        async with self._lock:
            if self._state not in ("awaiting_code", "awaiting_password"):
                return
            await self._teardown_client()
            self._state = "idle"
            self._phone = None
            self._phone_code_hash = None
            self._last_error = None

    async def logout(self) -> None:
        """Terminate the Telegram session and forget all in-memory state.

        Caller should also wipe the encrypted session row from the DB.
        """
        async with self._lock:
            if self._client is not None and self.logged_in:
                with contextlib.suppress(Exception):
                    await self._client.log_out()
            await self._teardown_client()
            self._state = "idle"
            self._phone = None
            self._phone_code_hash = None
            self._user_id = None
            self._username = None
            self._first_name = None
            self._last_error = None
            log.info("telegram.logout.ok")

    async def _teardown_client(self) -> None:
        if self._prime_task is not None and not self._prime_task.done():
            self._prime_task.cancel()
            with contextlib.suppress(Exception):
                await self._prime_task
        self._prime_task = None
        if self._client is None:
            return
        with contextlib.suppress(Exception):
            if getattr(self._client, "is_connected", False):
                await self._client.disconnect()
        with contextlib.suppress(Exception):
            await self._client.stop()
        self._client = None
        self._handler_attached = False

    # ------------------------------------------------------------------
    # Dialogs
    # ------------------------------------------------------------------

    async def list_dialogs(
        self,
        *,
        query: str | None = None,
        limit: int = 200,
    ) -> list[Dialog]:
        """Return the user's most recent dialogs, optionally filtered.

        We deliberately keep the projection small — the dashboard only
        needs enough to render a searchable list.
        """
        if not self.logged_in:
            raise TelegramManagerError("not logged in")
        assert self._client is not None

        needle = query.casefold().strip() if query else None
        out: list[Dialog] = []

        async for dialog in self._iter_dialogs(self._client, limit=limit):
            chat = dialog.chat
            title = chat.title or _join_name(chat) or str(chat.id)
            chat_type = _chat_type_str(chat.type)
            username = getattr(chat, "username", None)

            if needle is not None:
                hay = f"{title} {username or ''}".casefold()
                if needle not in hay:
                    continue

            out.append(
                Dialog(
                    chat_id=chat.id,
                    title=title,
                    chat_type=chat_type,
                    username=username,
                    members_count=getattr(chat, "members_count", None),
                    is_verified=bool(getattr(chat, "is_verified", False)),
                ),
            )
        return out

    @staticmethod
    async def _iter_dialogs(client: Client, *, limit: int) -> AsyncIterator[Any]:
        async for dialog in client.get_dialogs(limit=limit):
            yield dialog

    # ------------------------------------------------------------------
    # Live message handler (Phase 4 pipeline integration)
    # ------------------------------------------------------------------

    def set_message_callback(self, callback: MessageCallback | None) -> None:
        """Register the consumer for live incoming messages.

        Call once at lifespan boot with the Pipeline's dispatch entry
        point. ``None`` detaches the handler (the underlying Pyrogram
        Client keeps running so dialog/recent-messages calls still work).
        """
        self._on_message = callback
        if callback is None:
            self._handler_attached = False
            return
        self._attach_handler_if_pending()

    def _attach_handler_if_pending(self) -> None:
        """Attach the Pyrogram MessageHandler exactly once after login."""
        if self._handler_attached or self._client is None or self._on_message is None:
            return
        # Lazy imports — keeps the test path that monkeypatches
        # ``Client`` working even if pyrogram isn't fully importable.
        from pyrogram import handlers  # noqa: PLC0415
        from pyrogram.handlers import MessageHandler, RawUpdateHandler  # noqa: PLC0415

        async def _on_pyrogram_message(_client: Client, msg: Any) -> None:
            await self._handle_incoming(msg)

        async def _on_raw_update(
            _client: Client, update: Any, _users: Any, _chats: Any
        ) -> None:
            """Diagnostic-only: log the *type* of every raw update.
            Channel posts arrive as ``UpdateNewChannelMessage`` and only
            reach ``MessageHandler`` once the live client has resolved
            the channel's peer + access_hash. When channel updates are
            silently dropped (peer-cache miss, ``UpdateChannelTooLong``
            without a ``getChannelDifference`` follow-up), this is the
            only place we can see the raw event vs. the parsed message
            never arriving."""
            try:
                kind = type(update).__name__
                # Pull whatever peer ID is on the update so we can
                # correlate with the watched-chat list. Different
                # Telegram update types put the peer in different
                # places: ``UpdateChannelMessageViews`` has a flat
                # ``channel_id`` on the update itself, while
                # ``UpdateNewChannelMessage`` carries a full Message
                # whose ``peer_id`` is a PeerChannel/PeerUser/PeerChat.
                channel_id = getattr(update, "channel_id", None)
                msg = getattr(update, "message", None)
                peer_id = getattr(msg, "peer_id", None)
                if channel_id is None:
                    channel_id = getattr(peer_id, "channel_id", None)
                user_id = getattr(peer_id, "user_id", None)
                chat_id = getattr(peer_id, "chat_id", None)
                log.info(
                    "telegram.raw_update",
                    kind=kind,
                    channel_id=channel_id,
                    user_id=user_id,
                    chat_id=chat_id,
                )
            except Exception:  # pragma: no cover  (best-effort diagnostic)
                log.exception("telegram.raw_update.log_failed")

        try:
            self._client.add_handler(MessageHandler(_on_pyrogram_message))
            # The raw-update logger is only attached when explicitly
            # opted into — it fires for every channel-view-count tick,
            # which makes it noisy at scale. Toggle on when debugging
            # "channel posts not reaching the handler" issues.
            from autotrader.config import settings as _app_settings  # noqa: PLC0415
            if _app_settings.debug_telegram_raw_updates:
                self._client.add_handler(RawUpdateHandler(_on_raw_update))
        except Exception as exc:  # pragma: no cover  (handler API drift)
            log.warning("telegram.handler.attach_failed", error=str(exc))
            return
        self._handler_attached = True
        log.info("telegram.handler.attached")
        # Touch ``handlers`` to satisfy lint that the import is used.
        _ = handlers

    async def _prime_peer_cache(self, *, limit: int = 500) -> None:
        """Walk dialogs once so Pyrogram's session storage knows every
        channel/group the user is a member of, then explicitly resolve
        each watched channel so its update stream is subscribed.

        Two reasons this can't just be a single ``get_dialogs`` walk:

        1. Without *any* primer, update dispatch crashes with
           ``ValueError: Peer id invalid: -100…`` the moment a message
           arrives in a chat the in-memory session has never resolved.
        2. With ``in_memory=True``, Pyrogram loses the per-channel
           ``pts`` state on every restart. Pyrogram's update dispatcher
           silently drops ``UpdateNewChannelMessage`` events for
           channels it hasn't actively touched this session — even if
           the channel appears in ``get_dialogs``. ``get_chat_history``
           on each watched channel forces the resolve + ``getDifference``
           handshake that subscribes the live update stream.

        Cheap (the payload comes back over the same socket Pyrogram
        keeps warm) and idempotent — re-running just no-ops.
        """
        if self._client is None:
            return
        try:
            count = 0
            async for _ in self._client.get_dialogs(limit=limit):
                count += 1
            log.info("telegram.peer_cache.primed", dialogs=count)
        except Exception as exc:  # pragma: no cover - best-effort
            log.warning("telegram.peer_cache.failed", error=str(exc))
            return

        # Touch each watched channel/group so Pyrogram subscribes its
        # update stream. We pull the list lazily (avoids a hard import
        # cycle with the models package at module load) and ignore
        # failures per chat — a single broken row shouldn't block
        # subscription for the others.
        try:
            from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
            from autotrader.models.watched_channel import (  # noqa: PLC0415
                list_watched,
            )

            async with AsyncSessionLocal() as session:
                watched = await list_watched(session)
            enabled = [w for w in watched if w.enabled]
            subscribed = 0
            for w in enabled:
                try:
                    # ``get_chat_history(limit=1)`` is the cheapest call
                    # that forces a peer resolve AND touches the channel's
                    # pts state — exactly what the live update dispatcher
                    # needs to start routing posts to MessageHandler.
                    async for _ in self._client.get_chat_history(
                        w.chat_id, limit=1
                    ):
                        break
                    subscribed += 1
                except Exception as exc:
                    log.warning(
                        "telegram.peer_cache.subscribe_failed",
                        chat_id=w.chat_id,
                        title=w.title,
                        error=f"{type(exc).__name__}: {exc}",
                    )
            self._subscribed_chat_count = subscribed
            log.info(
                "telegram.peer_cache.subscribed",
                watched=len(enabled),
                subscribed=subscribed,
            )
        except Exception as exc:  # pragma: no cover - best-effort
            log.warning("telegram.peer_cache.subscribe_pass_failed",
                        error=f"{type(exc).__name__}: {exc}")

    async def _handle_incoming(self, msg: Any) -> None:
        """Convert a Pyrogram Message → IncomingMessage → callback."""
        if self._on_message is None:
            return
        chat = getattr(msg, "chat", None)
        chat_id = int(getattr(chat, "id", 0)) if chat is not None else 0
        kind, text = _extract_message_text(msg)
        # Observability: every Telegram update lands here. We log BEFORE
        # the empty-text early return so users can tell apart "no
        # messages arriving at all" (no log lines) from "stickers /
        # uncaptioned media filtered out" (skip lines).
        if not text:
            log.info(
                "telegram.message.skipped",
                chat_id=chat_id,
                kind=kind,
                reason="empty_text",
            )
            return
        from_user = getattr(msg, "from_user", None)
        sender_chat = getattr(msg, "sender_chat", None)
        sender_id = int(
            (getattr(from_user, "id", None) if from_user is not None else None)
            or (getattr(sender_chat, "id", None) if sender_chat is not None else None)
            or 0,
        )
        date = getattr(msg, "date", None) or datetime.now(UTC)
        # Update the dashboard health gauge — every accepted message
        # ticks the "we're alive" timestamp. The dashboard renders this
        # as "Last channel msg: <Xs ago>" so a Telegram-side hang is
        # visible at a glance.
        self._last_message_at = datetime.now(UTC)
        log.info(
            "telegram.message.received",
            chat_id=chat_id,
            sender_id=sender_id,
            kind=kind,
            text_len=len(text),
            text_preview=text[:80],
        )

        try:
            await self._on_message(
                IncomingMessage(
                    chat_id=chat_id,
                    sender_id=sender_id,
                    text=text,
                    media_kind=kind,
                    received_at=date,
                ),
            )
        except Exception as exc:  # pragma: no cover - consumer-side failure
            log.exception("telegram.handler.dispatch_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Recent messages (sample fodder for the parser builder)
    # ------------------------------------------------------------------

    async def recent_messages(
        self,
        chat_id: int,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch the last ``limit`` messages from ``chat_id``.

        Returns a list of dicts with ``id``, ``text``, ``media_kind``
        (``"text"`` / ``"caption"`` / ``"sticker"``), ``sender_id``, and
        ``date`` (ISO string).

        Sticker messages carry no ``text`` / ``caption`` but the
        sticker's emoji is what many channels use as the *direction*
        signal (👍 = call, 👎 = put), so we surface ``sticker.emoji``
        as the message text and tag it with ``media_kind="sticker"``.
        Other media (voice, video without caption, …) are skipped —
        the parser layer can't do anything with them.
        """
        if not self.logged_in:
            raise TelegramManagerError("not logged in")
        assert self._client is not None

        out: list[dict[str, Any]] = []
        async for msg in self._client.get_chat_history(chat_id, limit=limit):
            kind, text = _extract_message_text(msg)
            if not text:
                continue
            from_user = getattr(msg, "from_user", None)
            sender_chat = getattr(msg, "sender_chat", None)
            sender_id = (
                (from_user.id if from_user is not None else None)
                or (sender_chat.id if sender_chat is not None else None)
                or 0
            )
            date = getattr(msg, "date", None)
            out.append(
                {
                    "id": getattr(msg, "id", 0),
                    "text": text,
                    "media_kind": kind,
                    "sender_id": sender_id,
                    "date": date.isoformat() if date else None,
                },
            )
        return out


def _join_name(chat: Any) -> str:
    parts = [getattr(chat, "first_name", None), getattr(chat, "last_name", None)]
    return " ".join(p for p in parts if p)


def _extract_message_text(msg: Any) -> tuple[str, str]:
    """Return ``(media_kind, text)`` for a Pyrogram ``Message``.

    ``text`` is empty when the message is media we can't parse (voice,
    video without caption, …); the caller should skip those rows.

    ``media_kind`` distinguishes:

    * ``"text"``    — plain text message (``msg.text``).
    * ``"caption"`` — photo / video / document with a caption.
    * ``"sticker"`` — sticker; ``text`` is the sticker's emoji
                      (``msg.sticker.emoji``). This is the direction
                      signal in prep+sticker channels.
    * ``"other"``   — none of the above. ``text`` is empty.
    """
    text = getattr(msg, "text", None)
    if text:
        return "text", str(text)

    caption = getattr(msg, "caption", None)
    if caption:
        return "caption", str(caption)

    sticker = getattr(msg, "sticker", None)
    if sticker is not None:
        emoji = getattr(sticker, "emoji", None)
        if emoji:
            return "sticker", str(emoji)

    return "other", ""


def _chat_type_str(chat_type: Any) -> str:
    """Pyrogram exposes chat type as an enum; we want the lower-case name."""
    name = getattr(chat_type, "name", None) or getattr(chat_type, "value", None) or str(chat_type)
    return str(name).lower().rsplit(".", 1)[-1]
