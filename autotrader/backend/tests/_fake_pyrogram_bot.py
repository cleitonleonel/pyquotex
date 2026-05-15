"""Test double for the Pyrogram bot client.

We never spin up a real Pyrogram client in tests — they require network
+ a real bot token, and Pyrogram's session storage is an integration
hazard. ``FakePyrogramBot`` mimics just enough of the surface
``AdminBot`` calls to let us drive the bot end-to-end with canned
``Message`` / ``CallbackQuery`` updates.

Captured side-effects:
* ``sent_messages`` — list of (chat_id, text, reply_markup) tuples
* ``raise_on_send`` — set to an exception class to make the next
  send_message raise (used to test the 5-failure backoff)

Replay surface:
* ``await fake.fire_message(user_id, text)`` — invokes the registered
  message handler with a synthetic Message
* ``await fake.fire_callback(user_id, data)`` — same for callbacks
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeUser:
    id: int
    is_bot: bool = False
    first_name: str = "Tester"
    # See note on ``FakeChat.username`` — pyrofork's
    # ``MessageHandler.check_if_has_matching_listener`` also reads
    # ``from_user.username`` on every incoming message.
    username: str | None = None


@dataclass
class FakeChat:
    id: int
    type: str = "private"
    # Pyrofork's ``MessageHandler.check_if_has_matching_listener`` reads
    # ``chat.username`` (message_handler.py:67) before dispatching — bots
    # in private chats never have one, but the attribute access still
    # has to succeed. Default ``None`` matches what real Pyrogram returns
    # for private peers without a public @-handle.
    username: str | None = None


@dataclass
class FakeMessage:
    """Subset of pyrogram.types.Message that AdminBot reads."""

    text: str
    from_user: FakeUser
    chat: FakeChat
    id: int = 1

    async def reply_text(
        self,
        text: str,
        reply_markup: Any | None = None,
        **_kwargs: Any,
    ) -> "FakeMessage":
        # The fake bot stashes replies on the originating instance so
        # tests can assert on them. We also push to the bot's
        # ``sent_messages`` list via a back-reference set in
        # ``fire_message``.
        self._captured_reply = (text, reply_markup)  # type: ignore[attr-defined]
        if hasattr(self, "_bot"):
            self._bot.sent_messages.append(  # type: ignore[attr-defined]
                (self.chat.id, text, reply_markup),
            )
        return self


@dataclass
class FakeCallbackQuery:
    data: str
    from_user: FakeUser
    message: FakeMessage

    async def answer(self, text: str = "", **_kwargs: Any) -> None:
        self._captured_answer = text  # type: ignore[attr-defined]


MessageHandler = Callable[[Any, FakeMessage], Awaitable[None]]
CallbackHandler = Callable[[Any, FakeCallbackQuery], Awaitable[None]]


@dataclass
class FakePyrogramBot:
    """Minimal Pyrogram-Client substitute used by AdminBot tests."""

    bot_token: str = "fake-token"
    me_id: int = 99999
    started: bool = False
    sent_messages: list[tuple[int, str, Any]] = field(default_factory=list)
    raise_on_send: type[BaseException] | None = None
    _on_message: MessageHandler | None = None
    _on_callback: CallbackHandler | None = None
    _next_message_id: int = field(default=1, init=False)

    # ------------------------------------------------------------------
    # Lifecycle (matches the bits of pyrogram.Client AdminBot calls)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    def add_handler(self, handler: Any) -> None:
        # AdminBot wraps callbacks in MessageHandler / CallbackQueryHandler.
        # We sniff the wrapper class name to avoid importing pyrogram.
        kind = type(handler).__name__
        callback = getattr(handler, "callback", None)
        if callback is None:
            return
        if kind == "MessageHandler":
            self._on_message = callback
        elif kind == "CallbackQueryHandler":
            self._on_callback = callback

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: Any | None = None,
        **_kwargs: Any,
    ) -> Any:
        if self.raise_on_send is not None:
            exc_cls = self.raise_on_send
            self.raise_on_send = None  # one-shot unless reset
            raise exc_cls("fake send failure")
        msg_id = self._next_message_id
        self._next_message_id += 1
        self.sent_messages.append((chat_id, text, reply_markup))
        # Return a Pyrogram-shaped object so callers like the OTP relay
        # can capture the message_id from the round-trip (C1 fix).
        return type("Message", (), {"id": msg_id})()

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        **_kwargs: Any,
    ) -> None:
        """No-op stub — OTP relay tests verify edits via AdminBot.edit_message_text."""
        pass

    # ------------------------------------------------------------------
    # Replay surface (test-only)
    # ------------------------------------------------------------------

    async def fire_message(self, user_id: int, text: str) -> FakeMessage:
        msg = FakeMessage(
            text=text,
            from_user=FakeUser(id=user_id),
            chat=FakeChat(id=user_id),
        )
        msg._bot = self  # type: ignore[attr-defined]
        if self._on_message is not None:
            await self._on_message(self, msg)
        return msg

    async def fire_callback(self, user_id: int, data: str) -> FakeCallbackQuery:
        msg = FakeMessage(
            text="",
            from_user=FakeUser(id=user_id),
            chat=FakeChat(id=user_id),
        )
        msg._bot = self  # type: ignore[attr-defined]
        cq = FakeCallbackQuery(
            data=data,
            from_user=FakeUser(id=user_id),
            message=msg,
        )
        if self._on_callback is not None:
            await self._on_callback(self, cq)
        return cq
