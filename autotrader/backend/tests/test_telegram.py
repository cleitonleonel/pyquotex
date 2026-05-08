"""Telegram router + manager tests.

We swap ``pyrogram.Client`` for a small ``FakeTelegramClient`` whose
behaviour each test toggles via class-level knobs (mirrors the
``FakeQuotex`` pattern from ``test_broker.py``). The suite never
contacts Telegram.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any, ClassVar

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fake Pyrogram Client
# ---------------------------------------------------------------------------


class _SentCode:
    def __init__(self, hash_: str) -> None:
        self.phone_code_hash = hash_


class _FakeUser:
    def __init__(self) -> None:
        self.id = 42
        self.username = "trader"
        self.first_name = "Trader"
        self.phone_number = None


class _FakeChat:
    def __init__(
        self,
        chat_id: int,
        title: str,
        chat_type: str,
        username: str | None = None,
        members: int | None = None,
    ) -> None:
        self.id = chat_id
        self.title = title
        self.type = chat_type
        self.username = username
        self.members_count = members
        self.is_verified = False
        self.first_name = None
        self.last_name = None


class _FakeDialog:
    def __init__(self, chat: _FakeChat) -> None:
        self.chat = chat


class _FakeSticker:
    def __init__(self, emoji: str) -> None:
        self.emoji = emoji


class _FakeMessage:
    def __init__(
        self,
        msg_id: int,
        text: str | None = None,
        sender_id: int = 100,
        date: object | None = None,
        sticker: _FakeSticker | None = None,
        caption: str | None = None,
    ) -> None:
        self.id = msg_id
        self.text = text
        self.caption = caption
        self.sticker = sticker
        self.from_user = type("U", (), {"id": sender_id})()
        self.sender_chat = None
        self.date = date


class FakeTelegramClient:
    """Stand-in for ``pyrogram.Client``."""

    behavior: ClassVar[str] = "ok"     # "ok" | "needs_2fa" | "bad_code" | "bad_password"
    valid_code: ClassVar[str] = "11111"
    valid_password: ClassVar[str] = "p@ss"
    dialogs: ClassVar[list[_FakeChat]] = []
    history: ClassVar[dict[int, list[_FakeMessage]]] = {}
    last_instance: ClassVar[FakeTelegramClient | None] = None

    def __init__(
        self,
        name: str,
        api_id: int,
        api_hash: str,
        session_string: str | None = None,
        in_memory: bool = False,
        no_updates: bool = False,
    ) -> None:
        self.name = name
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string
        self.in_memory = in_memory
        self.no_updates = no_updates
        self.is_connected = False
        self._exported = "FAKE_SESSION_STRING_v1"
        FakeTelegramClient.last_instance = self

    async def connect(self) -> None:
        self.is_connected = True

    async def disconnect(self) -> None:
        self.is_connected = False

    async def start(self) -> None:
        self.is_connected = True

    async def stop(self) -> None:
        self.is_connected = False

    async def initialize(self) -> None:
        return None

    async def send_code(self, phone_number: str) -> _SentCode:
        return _SentCode("hash-deadbeef")

    async def sign_in(
        self,
        phone_number: str,
        phone_code_hash: str,
        phone_code: str,
    ) -> _FakeUser:
        if FakeTelegramClient.behavior == "bad_code":
            from pyrogram.errors import PhoneCodeInvalid  # noqa: PLC0415

            raise PhoneCodeInvalid("invalid code")
        if FakeTelegramClient.behavior == "needs_2fa":
            from pyrogram.errors import SessionPasswordNeeded  # noqa: PLC0415

            raise SessionPasswordNeeded("2fa needed")
        if phone_code != FakeTelegramClient.valid_code:
            from pyrogram.errors import PhoneCodeInvalid  # noqa: PLC0415

            raise PhoneCodeInvalid("invalid code")
        return _FakeUser()

    async def check_password(self, password: str) -> _FakeUser:
        if password != FakeTelegramClient.valid_password:
            from pyrogram.errors import PasswordHashInvalid  # noqa: PLC0415

            raise PasswordHashInvalid("bad password")
        return _FakeUser()

    async def get_me(self) -> _FakeUser:
        return _FakeUser()

    async def export_session_string(self) -> str:
        return self._exported

    async def log_out(self) -> bool:
        return True

    async def get_dialogs(self, limit: int = 200) -> AsyncIterator[_FakeDialog]:
        for chat in FakeTelegramClient.dialogs:
            yield _FakeDialog(chat)

    async def get_chat_history(
        self,
        chat_id: int,
        limit: int = 0,
    ) -> AsyncIterator[_FakeMessage]:
        msgs = FakeTelegramClient.history.get(chat_id, [])
        for msg in msgs[: limit or len(msgs)]:
            yield msg


@pytest.fixture(autouse=True)
def _reset_fake_telegram() -> Iterator[None]:
    FakeTelegramClient.behavior = "ok"
    FakeTelegramClient.valid_code = "11111"
    FakeTelegramClient.valid_password = "p@ss"
    FakeTelegramClient.dialogs = [
        _FakeChat(-1001, "Signals Pro", "channel", username="signalspro", members=1234),
        _FakeChat(-1002, "VIP Group", "supergroup", username=None, members=42),
        _FakeChat(7, "Mom", "private"),
    ]
    FakeTelegramClient.history = {
        -1001: [
            # Newest first (Pyrogram's get_chat_history default order).
            _FakeMessage(103, sticker=_FakeSticker("👍"), sender_id=200),
            _FakeMessage(
                102,
                text="🌐 PAIR: USD NGN OTC\n⏱️ TIME: 01 Minute",
                sender_id=200,
            ),
            _FakeMessage(101, "🟢 BUY EUR/USD 1m", sender_id=200),
            _FakeMessage(100, "Good morning everyone!", sender_id=200),
            # Pure media with no caption / sticker — should be skipped.
            _FakeMessage(99, sender_id=200),
        ],
    }
    FakeTelegramClient.last_instance = None
    yield


@pytest.fixture
def fake_telegram_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[FakeTelegramClient]]:
    monkeypatch.setattr(
        "autotrader.services.telegram_manager.Client",
        FakeTelegramClient,
    )
    yield FakeTelegramClient


# ---------------------------------------------------------------------------
# TestClient + DB cleanup
# ---------------------------------------------------------------------------


@pytest.fixture
def client(
    fake_telegram_client: type[FakeTelegramClient],
) -> Iterator[TestClient]:
    from autotrader.db import AsyncSessionLocal, engine  # noqa: PLC0415
    from autotrader.main import app  # noqa: PLC0415
    from autotrader.models.telegram_session import TelegramSession  # noqa: PLC0415
    from autotrader.models.watched_channel import WatchedChannel  # noqa: PLC0415

    with TestClient(app) as c:
        yield c

    from sqlmodel import delete  # noqa: PLC0415

    async def _wipe() -> None:
        manager = app.state.telegram_manager
        await manager.cancel_login()
        if manager.logged_in:
            await manager.logout()
        async with AsyncSessionLocal() as s:
            await s.exec(delete(TelegramSession))  # type: ignore[call-overload]
            await s.exec(delete(WatchedChannel))  # type: ignore[call-overload]
            await s.commit()
        await engine.dispose()

    asyncio.new_event_loop().run_until_complete(_wipe())


def _login(client: TestClient) -> dict[str, str]:
    r = client.post("/auth/login", json={"passcode": "test-passcode"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


def test_status_requires_auth(client: TestClient) -> None:
    r = client.get("/telegram/status")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_starts_idle(client: TestClient) -> None:
    headers = _login(client)
    r = client.get("/telegram/status", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "idle"
    assert body["logged_in"] is False
    assert body["awaiting_code"] is False


# ---------------------------------------------------------------------------
# Login flow — happy path (no 2FA)
# ---------------------------------------------------------------------------


def test_login_phone_code_no_2fa(client: TestClient) -> None:
    headers = _login(client)

    r = client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "awaiting_code"
    assert body["phone_masked"] is not None

    r = client.post("/telegram/code", headers=headers, json={"code": "11111"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "logged_in"
    assert body["logged_in"] is True
    assert body["user_id"] == 42
    assert body["username"] == "trader"


# ---------------------------------------------------------------------------
# Login flow — wrong code stays in awaiting_code so the user can retry
# ---------------------------------------------------------------------------


def test_login_wrong_code_can_retry(client: TestClient) -> None:
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})

    r = client.post("/telegram/code", headers=headers, json={"code": "99999"})
    assert r.status_code == 400
    assert "invalid" in r.json()["detail"].lower()

    r = client.get("/telegram/status", headers=headers)
    assert r.json()["state"] == "awaiting_code"

    r = client.post("/telegram/code", headers=headers, json={"code": "11111"})
    assert r.status_code == 200
    assert r.json()["state"] == "logged_in"


# ---------------------------------------------------------------------------
# Login flow — 2FA branch
# ---------------------------------------------------------------------------


def test_login_with_2fa(client: TestClient) -> None:
    FakeTelegramClient.behavior = "needs_2fa"
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})

    # Submitting the SMS code transitions to awaiting_password.
    r = client.post("/telegram/code", headers=headers, json={"code": "11111"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "awaiting_password"
    assert body["awaiting_password"] is True

    # Wrong password does not advance.
    r = client.post("/telegram/password", headers=headers, json={"password": "nope"})
    assert r.status_code == 400

    r = client.post("/telegram/password", headers=headers, json={"password": "p@ss"})
    assert r.status_code == 200
    assert r.json()["state"] == "logged_in"


# ---------------------------------------------------------------------------
# Cancel + logout
# ---------------------------------------------------------------------------


def test_cancel_in_flight_login(client: TestClient) -> None:
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})

    r = client.post("/telegram/cancel", headers=headers)
    assert r.status_code == 200
    r = client.get("/telegram/status", headers=headers)
    assert r.json()["state"] == "idle"


def test_logout(client: TestClient) -> None:
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    r = client.post("/telegram/logout", headers=headers)
    assert r.status_code == 200
    r = client.get("/telegram/status", headers=headers)
    assert r.json()["state"] == "idle"
    assert r.json()["logged_in"] is False


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


def test_dialogs_requires_login(client: TestClient) -> None:
    headers = _login(client)
    r = client.get("/telegram/dialogs", headers=headers)
    assert r.status_code == 409


def test_dialogs_returns_list(client: TestClient) -> None:
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    r = client.get("/telegram/dialogs", headers=headers)
    assert r.status_code == 200
    body = r.json()
    titles = [d["title"] for d in body]
    assert "Signals Pro" in titles
    assert "VIP Group" in titles
    # All start out unwatched.
    assert all(d["watched"] is False for d in body)


def test_dialogs_search(client: TestClient) -> None:
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    r = client.get("/telegram/dialogs?q=signal", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["title"] == "Signals Pro"


# ---------------------------------------------------------------------------
# Watch list
# ---------------------------------------------------------------------------


def test_watch_then_unwatch(client: TestClient) -> None:
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    r = client.post(
        "/telegram/watch",
        headers=headers,
        json={
            "chat_id": -1001,
            "title": "Signals Pro",
            "chat_type": "channel",
            "username": "signalspro",
            "enabled": True,
        },
    )
    assert r.status_code == 200

    # ``/dialogs`` should now reflect the watch flag.
    r = client.get("/telegram/dialogs", headers=headers)
    body = r.json()
    target = next(d for d in body if d["chat_id"] == -1001)
    assert target["watched"] is True

    # ``/watched`` lists the saved entry.
    r = client.get("/telegram/watched", headers=headers)
    body = r.json()
    assert len(body) == 1
    assert body[0]["chat_id"] == -1001

    # Unwatch.
    r = client.delete("/telegram/watch/-1001", headers=headers)
    assert r.status_code == 200
    r = client.get("/telegram/watched", headers=headers)
    assert r.json() == []


def test_watch_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/telegram/watch",
        json={
            "chat_id": -1001,
            "title": "x",
            "chat_type": "channel",
            "username": None,
            "enabled": True,
        },
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Session persistence (DB row written after login)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Recent messages
# ---------------------------------------------------------------------------


def test_messages_requires_login(client: TestClient) -> None:
    headers = _login(client)
    r = client.get("/telegram/messages?chat_id=-1001", headers=headers)
    assert r.status_code == 409


def test_messages_blocked_for_unwatched(client: TestClient) -> None:
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    r = client.get("/telegram/messages?chat_id=-1001", headers=headers)
    assert r.status_code == 403


def test_messages_for_watched_chat(client: TestClient) -> None:
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    client.post(
        "/telegram/watch",
        headers=headers,
        json={
            "chat_id": -1001,
            "title": "Signals Pro",
            "chat_type": "channel",
            "username": "signalspro",
            "enabled": True,
        },
    )

    r = client.get("/telegram/messages?chat_id=-1001&limit=10", headers=headers)
    assert r.status_code == 200
    body = r.json()

    # Stickers must surface as their emoji; pure media is dropped.
    kinds = [m["media_kind"] for m in body]
    texts = [m["text"] for m in body]
    assert "sticker" in kinds
    assert "👍" in texts
    # Prep + plain text both come through.
    assert any("PAIR:" in t for t in texts)
    assert any(t == "🟢 BUY EUR/USD 1m" for t in texts)
    # Media-only message (id=99) was skipped.
    assert all(m["id"] != 99 for m in body)


def test_messages_sticker_emoji_alone(client: TestClient) -> None:
    """A solo sticker is the direction signal in prep+sticker channels."""
    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})
    client.post(
        "/telegram/watch",
        headers=headers,
        json={
            "chat_id": -1001,
            "title": "Signals Pro",
            "chat_type": "channel",
            "username": "signalspro",
            "enabled": True,
        },
    )

    r = client.get("/telegram/messages?chat_id=-1001&limit=10", headers=headers)
    body = r.json()
    sticker_msg = next(m for m in body if m["media_kind"] == "sticker")
    assert sticker_msg["text"] == "👍"


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------


def test_session_persisted_after_login(client: TestClient) -> None:
    from autotrader.db import AsyncSessionLocal  # noqa: PLC0415
    from autotrader.models.telegram_session import load_session  # noqa: PLC0415

    headers = _login(client)
    client.post("/telegram/login", headers=headers, json={"phone": "+15550100"})
    client.post("/telegram/code", headers=headers, json={"code": "11111"})

    async def _check() -> Any:
        async with AsyncSessionLocal() as s:
            return await load_session(s)

    row = asyncio.new_event_loop().run_until_complete(_check())
    assert row is not None
    assert row.phone == "+15550100"
    assert row.user_id == 42
    # Session string is encrypted at rest; decrypt and check.
    assert row.session_string() == "FAKE_SESSION_STRING_v1"


# ---------------------------------------------------------------------------
# Pyrogram peer-id range patch
# ---------------------------------------------------------------------------


def test_pyrogram_min_channel_id_patched_for_64bit_channels() -> None:
    """Pyrogram 2.0.106 rejects modern 64-bit Telegram channel IDs.

    The TelegramManager module monkey-patches ``MIN_CHANNEL_ID`` to a
    big-enough lower bound. If that patch is reverted (or pyrogram is
    re-imported in a way that doesn't pick it up) live update dispatch
    silently breaks for any channel past ``-1_002_147_483_647``, which
    is most channels created in the last few years.
    """
    import pyrogram.utils  # noqa: PLC0415

    # Real-world channel IDs that crashed the live handler before the
    # patch — both are within the patched range.
    for channel_id in (-1002218147270, -1002475564994):
        # Should not raise.
        assert pyrogram.utils.get_peer_type(channel_id) == "channel"
