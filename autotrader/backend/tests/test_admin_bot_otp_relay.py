"""AdminBotOTPRelay — OTP-message lifecycle in Telegram.

We never instantiate a real Pyrogram client here. ``FakeAdminBot``
captures every ``send`` / ``edit`` call so tests assert on the
resulting message sequence directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _SentMessage:
    chat_id: int
    text: str
    message_id: int


@dataclass
class _EditedMessage:
    chat_id: int
    message_id: int
    text: str


class FakeAdminBot:
    """Captures send/edit_message_text. State == 'running' by default."""

    def __init__(self, state: str = "running") -> None:
        self._state = state
        self._next_message_id = 1000
        self.sent: list[_SentMessage] = []
        self.edits: list[_EditedMessage] = []

    def status(self) -> Any:
        return type("S", (), {"state": self._state})()

    async def send(self, chat_id: int, text: str, **_kwargs: Any) -> Any:
        msg_id = self._next_message_id
        self._next_message_id += 1
        self.sent.append(_SentMessage(chat_id=chat_id, text=text, message_id=msg_id))
        # Return a Pyrogram-shaped object with id attribute.
        return type("M", (), {"id": msg_id})()

    async def edit_message_text(
        self, chat_id: int, message_id: int, text: str, **_kwargs: Any,
    ) -> None:
        self.edits.append(
            _EditedMessage(chat_id=chat_id, message_id=message_id, text=text),
        )


@dataclass
class FakeManager:
    submitted: list[str] = field(default_factory=list)

    async def submit_otp(self, code: str) -> None:
        self.submitted.append(code)


@pytest.fixture
def fake_bot() -> FakeAdminBot:
    return FakeAdminBot()


@pytest.fixture
def fake_manager() -> FakeManager:
    return FakeManager()


def _relay(fake_bot: FakeAdminBot, fake_manager: FakeManager, bound_user_id: int = 42):
    from autotrader.services.admin_bot_otp_relay import AdminBotOTPRelay  # noqa: PLC0415

    return AdminBotOTPRelay(
        manager=fake_manager,
        admin_bot=fake_bot,
        bound_user_id=bound_user_id,
        max_attempts=3,
    )


# ---------------------------------------------------------------------------
# on_otp_required (attempt 1) — sends a fresh message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_otp_required_attempt_1_sends_message(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("Enter PIN from email", attempt=1)

    assert len(fake_bot.sent) == 1
    msg = fake_bot.sent[0]
    assert msg.chat_id == 42
    assert "OTP" in msg.text or "PIN" in msg.text
    assert "reply" in msg.text.lower()
    assert fake_bot.edits == []  # no edits on attempt 1


@pytest.mark.asyncio
async def test_on_otp_required_attempt_2_edits_existing_message(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """Re-prompt (attempt > 1) edits the SAME message — the same
    message_id stays valid as the operator's reply target."""
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("first prompt", attempt=1)
    sent_id = fake_bot.sent[0].message_id

    await relay.on_otp_required("second prompt", attempt=2)

    assert len(fake_bot.sent) == 1  # still only ONE send
    assert len(fake_bot.edits) == 1
    edit = fake_bot.edits[0]
    assert edit.message_id == sent_id
    assert "2/3" in edit.text  # attempt counter shows up


@pytest.mark.asyncio
async def test_disabled_bot_short_circuits(
    fake_manager: FakeManager,
) -> None:
    """When admin_bot.status().state != 'running', the relay no-ops
    silently — the dashboard's awaiting_otp surface is the fallback."""
    bot = FakeAdminBot(state="disabled")
    relay = _relay(bot, fake_manager)
    await relay.on_otp_required("prompt", attempt=1)

    assert bot.sent == []
    assert bot.edits == []
