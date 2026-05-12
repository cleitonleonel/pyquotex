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


# ---------------------------------------------------------------------------
# handle_reply — extract digits + submit
# ---------------------------------------------------------------------------


@dataclass
class _FakeReplyTo:
    id: int


@dataclass
class _FakeFromUser:
    id: int


@dataclass
class _FakeMessage:
    text: str
    reply_to_message: _FakeReplyTo | None
    from_user: _FakeFromUser


def _reply(text: str, target_id: int, user_id: int = 42) -> _FakeMessage:
    return _FakeMessage(
        text=text,
        reply_to_message=_FakeReplyTo(id=target_id),
        from_user=_FakeFromUser(id=user_id),
    )


@pytest.mark.asyncio
async def test_owns_reply_returns_true_only_for_active_message_id(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """The admin_bot_commands hook asks the relay 'is this yours?'
    before handing the message over. Yes only when there's an active
    cycle and the reply_to_message.id matches."""
    relay = _relay(fake_bot, fake_manager)
    # No active cycle → never owns.
    assert relay.owns_reply(_reply("123456", target_id=9999)) is False

    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    assert relay.owns_reply(_reply("123456", target_id=active_id)) is True
    assert relay.owns_reply(_reply("123456", target_id=active_id + 1)) is False
    # Message with no reply_to_message is not ours.
    assert relay.owns_reply(
        _FakeMessage(text="123456", reply_to_message=None, from_user=_FakeFromUser(id=42)),
    ) is False


@pytest.mark.asyncio
async def test_handle_reply_extracts_digits_and_submits(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    await relay.handle_reply(_reply("code: 123456 (got it)", target_id=active_id))

    assert fake_manager.submitted == ["123456"]


@pytest.mark.asyncio
async def test_handle_reply_with_no_digits_edits_helper_message(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """A reply without 4–8 contiguous digits is a fat-finger. We edit
    the message to nudge, without burning an attempt slot."""
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    await relay.handle_reply(_reply("hello what", target_id=active_id))

    assert fake_manager.submitted == []
    assert len(fake_bot.edits) == 1
    assert "no digits" in fake_bot.edits[0].text.lower()


@pytest.mark.asyncio
async def test_handle_reply_with_wrong_target_is_ignored(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """Defensive: even if the commands hook somehow forwards a reply
    that doesn't target our message, we drop it silently."""
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    await relay.handle_reply(_reply("123456", target_id=active_id + 99))

    assert fake_manager.submitted == []
    assert fake_bot.edits == []


@pytest.mark.asyncio
async def test_handle_reply_when_idle_is_ignored(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    relay = _relay(fake_bot, fake_manager)
    # Never called on_otp_required → cycle is idle.

    await relay.handle_reply(_reply("123456", target_id=12345))

    assert fake_manager.submitted == []
    assert fake_bot.edits == []


@pytest.mark.asyncio
async def test_handle_reply_submit_failure_edits_error_message(
    fake_bot: FakeAdminBot,
) -> None:
    """Spec contract: when manager.submit_otp raises, handle_reply
    must swallow the exception, edit the Telegram message to an
    operator-facing error, and never propagate. Future regression
    that drops the except block would otherwise slip through."""
    from autotrader.services.admin_bot_otp_relay import AdminBotOTPRelay  # noqa: PLC0415

    class _RaisingManager:
        async def submit_otp(self, code: str) -> None:
            raise RuntimeError("broker timeout")

    raising_manager = _RaisingManager()
    relay = AdminBotOTPRelay(
        manager=raising_manager,
        admin_bot=fake_bot,
        bound_user_id=42,
        max_attempts=3,
    )
    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    # Must NOT raise — exception is swallowed at the handle_reply boundary.
    await relay.handle_reply(_reply("123456", target_id=active_id))

    # Operator-facing error edit is present.
    assert len(fake_bot.edits) == 1
    edit_text = fake_bot.edits[0].text.lower()
    assert "internal error" in edit_text
    assert "/reconnect" in edit_text


# ---------------------------------------------------------------------------
# on_otp_resolved + on_otp_timeout + max-attempts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_otp_resolved_edits_to_connected_and_clears_cycle(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    await relay.on_otp_resolved()

    # The terminal edit shows up.
    assert any(
        edit.message_id == active_id and ("connected" in edit.text.lower())
        for edit in fake_bot.edits
    )
    # And the cycle is cleared — a stale reply now is ignored.
    fake_manager.submitted.clear()
    await relay.handle_reply(_reply("123456", target_id=active_id))
    assert fake_manager.submitted == []


@pytest.mark.asyncio
async def test_on_otp_timeout_edits_to_expired_and_clears_cycle(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    active_id = fake_bot.sent[0].message_id

    await relay.on_otp_timeout()

    assert any(
        edit.message_id == active_id and ("expired" in edit.text.lower())
        for edit in fake_bot.edits
    )
    assert relay.owns_reply(_reply("123456", target_id=active_id)) is False


@pytest.mark.asyncio
async def test_attempts_cap_exhausted_edits_terminal_and_stops_accepting(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """After ``max_attempts`` re-prompts in a row, the relay edits to
    a terminal '/reconnect to retry' message and refuses further
    replies until a fresh cycle (attempt=1) starts."""
    relay = _relay(fake_bot, fake_manager)  # default max_attempts=3
    await relay.on_otp_required("p1", attempt=1)
    active_id = fake_bot.sent[0].message_id
    await relay.on_otp_required("p2", attempt=2)
    await relay.on_otp_required("p3", attempt=3)
    # The 4th prompt — beyond the cap — must lock down the cycle.
    await relay.on_otp_required("p4", attempt=4)

    # Last edit is the terminal message.
    last_edit = fake_bot.edits[-1]
    assert last_edit.message_id == active_id
    assert "/reconnect" in last_edit.text.lower()
    # And replies are now dropped.
    fake_manager.submitted.clear()
    await relay.handle_reply(_reply("123456", target_id=active_id))
    assert fake_manager.submitted == []


@pytest.mark.asyncio
async def test_max_attempts_env_var_changes_cap(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """With max_attempts=5, attempts 4 and 5 are still soft retries —
    only attempt=6 triggers the terminal edit."""
    relay = _relay(fake_bot, fake_manager)
    # Replace with a higher cap.
    from autotrader.services.admin_bot_otp_relay import AdminBotOTPRelay  # noqa: PLC0415
    relay = AdminBotOTPRelay(
        manager=fake_manager,
        admin_bot=fake_bot,
        bound_user_id=42,
        max_attempts=5,
    )

    await relay.on_otp_required("p", attempt=1)
    for n in range(2, 6):
        await relay.on_otp_required("p", attempt=n)

    # Attempts 1..5 are within the cap → no '/reconnect' edit yet.
    edits_so_far = " | ".join(e.text for e in fake_bot.edits)
    assert "/reconnect" not in edits_so_far

    await relay.on_otp_required("p", attempt=6)
    assert "/reconnect" in fake_bot.edits[-1].text.lower()


@pytest.mark.asyncio
async def test_fresh_attempt_1_after_terminal_replaces_cycle(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """After the operator hits /reconnect and the manager triggers a
    fresh begin_connect → fresh on_otp_required(attempt=1) — the relay
    sends a NEW message rather than editing the dead one."""
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    # Force the terminal edit via exhausted attempts.
    for n in range(2, 5):
        await relay.on_otp_required("p", attempt=n)
    assert "/reconnect" in fake_bot.edits[-1].text.lower()

    first_sent = fake_bot.sent[0].message_id

    # Fresh cycle.
    await relay.on_otp_required("fresh prompt", attempt=1)
    assert len(fake_bot.sent) == 2
    assert fake_bot.sent[1].message_id != first_sent


@pytest.mark.asyncio
async def test_on_otp_resolved_is_idempotent(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """Second consecutive on_otp_resolved must be a no-op (guarded by
    ``if self._active is None: return``). Future refactors that
    accidentally drop the guard would otherwise crash the relay on
    races."""
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    await relay.on_otp_resolved()
    edit_count_after_first = len(fake_bot.edits)

    # Second call must not raise and must produce no additional edits.
    await relay.on_otp_resolved()
    assert len(fake_bot.edits) == edit_count_after_first


@pytest.mark.asyncio
async def test_on_otp_timeout_is_idempotent(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """Same contract as on_otp_resolved — second timeout call is a no-op."""
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    await relay.on_otp_timeout()
    edit_count_after_first = len(fake_bot.edits)

    await relay.on_otp_timeout()
    assert len(fake_bot.edits) == edit_count_after_first
