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
        self._bound_user_id: int | None = 42  # default for existing tests
        self.sent: list[_SentMessage] = []
        self.edits: list[_EditedMessage] = []

    def status(self) -> Any:
        return type("S", (), {"state": self._state, "bound_user_id": self._bound_user_id})()

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
async def test_attempts_cap_exhausted_sends_terminal_and_stops_accepting(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """After ``max_attempts`` re-prompts in a row, the relay fires a
    terminal '/reconnect' alert and refuses further replies until a
    fresh cycle starts via /reconnect.

    Note: the entry-point cap check (Fix A, 2026-05-12) fires a fresh
    ``send`` rather than editing the active message — the operator's
    inbox should see the loud "gave up" alert as a NEW message, and
    the previous prompt edits become inert."""
    relay = _relay(fake_bot, fake_manager)  # default max_attempts=3
    await relay.on_otp_required("p1", attempt=1)
    active_id = fake_bot.sent[0].message_id
    await relay.on_otp_required("p2", attempt=2)
    await relay.on_otp_required("p3", attempt=3)
    # The 4th prompt — beyond the cap — must lock down the cycle.
    await relay.on_otp_required("p4", attempt=4)

    # Terminal alert arrives as a fresh send (not an edit of the
    # active prompt). It names the cap and asks for /reconnect.
    assert len(fake_bot.sent) == 2  # initial prompt + exhaustion alert
    terminal = fake_bot.sent[-1]
    assert "/reconnect" in terminal.text.lower()
    assert "gave up" in terminal.text.lower()
    # And replies targeting the dead prompt are now dropped.
    fake_manager.submitted.clear()
    await relay.handle_reply(_reply("123456", target_id=active_id))
    assert fake_manager.submitted == []


@pytest.mark.asyncio
async def test_max_attempts_env_var_changes_cap(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """With max_attempts=5, attempts 4 and 5 are still soft retries —
    only attempt=6 triggers the terminal exhaustion alert (send)."""
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

    # Attempts 1..5 are within the cap → no exhaustion send yet.
    assert len(fake_bot.sent) == 1
    edits_so_far = " | ".join(e.text for e in fake_bot.edits)
    assert "gave up" not in edits_so_far.lower()

    await relay.on_otp_required("p", attempt=6)
    # Now the exhaustion alert lands as a fresh send.
    assert len(fake_bot.sent) == 2
    assert "gave up" in fake_bot.sent[-1].text.lower()
    assert "/reconnect" in fake_bot.sent[-1].text.lower()


@pytest.mark.asyncio
async def test_fresh_attempt_1_after_terminal_replaces_cycle(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """After the operator hits /reconnect (which calls
    :meth:`reset_exhaustion`) and the manager triggers a fresh
    begin_connect → fresh on_otp_required(attempt=1) — the relay
    sends a NEW message rather than editing the dead one."""
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    # Force the terminal exhaustion alert.
    for n in range(2, 5):
        await relay.on_otp_required("p", attempt=n)
    assert "gave up" in fake_bot.sent[-1].text.lower()

    first_sent = fake_bot.sent[0].message_id
    sends_at_exhaustion = len(fake_bot.sent)

    # /reconnect clears the latch via reset_exhaustion (the public hook
    # the manager's reset_for_manual_reconnect drives).
    relay.reset_exhaustion()

    # Fresh cycle.
    await relay.on_otp_required("fresh prompt", attempt=1)
    assert len(fake_bot.sent) == sends_at_exhaustion + 1
    assert fake_bot.sent[-1].message_id != first_sent


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


# ---------------------------------------------------------------------------
# Regression tests: final holistic review C1 + C2
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relay_captures_real_message_id_through_admin_bot(
    fake_manager: FakeManager,
) -> None:
    """REGRESSION (final-review C1): AdminBot.send used to return None,
    so the relay captured message_id=0 and every edit/reply was broken.
    This test wires the real AdminBot through FakePyrogramBot and asserts
    the relay sees a non-zero message_id."""
    from autotrader.services.admin_bot import AdminBot  # noqa: PLC0415
    from autotrader.services.admin_bot_otp_relay import AdminBotOTPRelay  # noqa: PLC0415
    from tests._fake_pyrogram_bot import FakePyrogramBot  # noqa: PLC0415

    fake_client_factory = lambda _token: FakePyrogramBot()  # noqa: E731
    bot = AdminBot(
        bot_token="test-token",
        client_factory=fake_client_factory,
        bound_user_id=42,
    )
    await bot.start()
    assert bot.status().state == "running"

    relay = AdminBotOTPRelay(
        manager=fake_manager,
        admin_bot=bot,
        bound_user_id=42,
        max_attempts=3,
    )
    await relay.on_otp_required("prompt", attempt=1)

    # The relay's active cycle must have captured a real, non-zero
    # message_id from the bot's send round-trip.
    assert relay._active is not None
    assert relay._active.message_id != 0, (
        f"relay captured message_id=0 — AdminBot.send is dropping the "
        f"Message return value. Active cycle: {relay._active}"
    )

    await bot.stop()


@pytest.mark.asyncio
async def test_relay_picks_up_bound_user_id_after_start_command(
    fake_manager: FakeManager,
) -> None:
    """REGRESSION (final-review C2): relay used to cache bound_user_id
    at __init__. On first-deploy where /start runs AFTER lifespan, the
    relay was permanently locked out. Now it reads lazily."""
    bot = FakeAdminBot(state="running")
    # Bot starts WITHOUT a bound user.
    bot._bound_user_id = None  # simulate ctor with bound_user_id=None

    relay = _relay(bot, fake_manager, bound_user_id=None)
    await relay.on_otp_required("prompt", attempt=1)
    # No bound user → silent skip.
    assert bot.sent == []

    # Operator runs /start, which calls set_bound_user_id on the bot.
    bot._bound_user_id = 42  # simulate post-construction /start binding

    await relay.on_otp_required("prompt 2", attempt=1)
    # Now the relay picks up the new binding.
    assert len(bot.sent) == 1
    assert bot.sent[0].chat_id == 42


# ---------------------------------------------------------------------------
# Fix A — broker-disconnect blindness: tight OTP-attempt cap at the entry
# point. The old cap-check lived inside ``_bump_existing_cycle`` and was
# bypassed whenever a 180s timeout cleared ``_active``, because the next
# prompt then routed through ``_start_new_cycle`` (which never checked
# the cap). Production incident 2026-05-12: five OTP emails in 13min.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relay_sends_single_exhaustion_alert_then_goes_quiet(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """When the cumulative attempt counter exceeds ``max_attempts``, the
    relay must fire ONE final 'gave up' alert to the operator and then
    suppress every subsequent prompt until the cycle is reset.

    Models the production failure mode where pyquotex's internal
    supervisor regenerates a fresh PIN email on every retry. After we
    stop relaying, the manager (Fix C) halts the supervisor — so the
    relay's silence is what keeps the operator's inbox from drowning.
    """
    relay = _relay(fake_bot, fake_manager)  # default max_attempts=3

    # Drive each attempt as if it were a separate broker challenge —
    # mimics the production path where every OTP timeout drops
    # ``_active`` to None, so the next prompt routes through
    # ``_start_new_cycle``. We simulate that by clearing _active
    # between attempts (which is exactly what ``on_otp_timeout`` does).
    for n in range(1, 4):
        await relay.on_otp_required(f"prompt {n}", attempt=n)
        await relay.on_otp_timeout()

    # 3 sends + 3 timeout-edits so far. Snapshot before the cap fires.
    sends_so_far = len(fake_bot.sent)
    edits_so_far = len(fake_bot.edits)
    assert sends_so_far == 3

    # Attempt 4 — over the cap. Must trigger exactly ONE exhaustion
    # alert (via send, since _active was cleared) and no new prompt.
    await relay.on_otp_required("prompt 4", attempt=4)
    assert len(fake_bot.sent) == sends_so_far + 1, (
        "expected exactly one extra send for the exhaustion alert; "
        f"got sends={fake_bot.sent[sends_so_far:]}"
    )
    alert = fake_bot.sent[-1]
    assert "gave up" in alert.text.lower()
    assert "3" in alert.text  # the cap value is named
    assert "/reconnect" in alert.text.lower()

    # Attempt 5 — must be silently suppressed.
    await relay.on_otp_required("prompt 5", attempt=5)
    assert len(fake_bot.sent) == sends_so_far + 1, (
        "attempt 5 should be silent — got new sends "
        f"{fake_bot.sent[sends_so_far + 1:]}"
    )
    assert len(fake_bot.edits) == edits_so_far, (
        "attempt 5 should be silent — got new edits "
        f"{fake_bot.edits[edits_so_far:]}"
    )


@pytest.mark.asyncio
async def test_relay_exhaustion_resets_on_resolved(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """A successful resolve clears the exhaustion latch — verified
    here by triggering exhaustion, calling on_otp_resolved (terminal
    edit), and then re-triggering exhaustion to confirm the latch fires
    a SECOND alert (it wouldn't if the latch had stayed set)."""
    relay = _relay(fake_bot, fake_manager)
    # Trigger exhaustion.
    for n in range(1, 4):
        await relay.on_otp_required(f"p{n}", attempt=n)
        await relay.on_otp_timeout()
    await relay.on_otp_required("p4", attempt=4)
    assert "gave up" in fake_bot.sent[-1].text.lower()
    sends_at_first_exhaustion = len(fake_bot.sent)

    # While still latched, another over-cap call must be silent.
    await relay.on_otp_required("p5", attempt=5)
    assert len(fake_bot.sent) == sends_at_first_exhaustion

    # Resolve clears the latch (and there's no active cycle, so the
    # method short-circuits without an edit — that's fine).
    await relay.on_otp_resolved()
    # Now another over-cap call should fire a fresh alert (latch cleared).
    await relay.on_otp_required("p6", attempt=4)
    assert len(fake_bot.sent) == sends_at_first_exhaustion + 1
    assert "gave up" in fake_bot.sent[-1].text.lower()


@pytest.mark.asyncio
async def test_relay_exhaustion_resets_on_explicit_reset(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """``reset_exhaustion()`` is the public hook the manager's
    :meth:`reset_for_manual_reconnect` calls. It must clear the latch
    so a follow-up over-cap call fires a fresh alert."""
    relay = _relay(fake_bot, fake_manager)
    for n in range(1, 4):
        await relay.on_otp_required(f"p{n}", attempt=n)
        await relay.on_otp_timeout()
    await relay.on_otp_required("p4", attempt=4)
    sends_at_exhaustion = len(fake_bot.sent)

    # While still latched, over-cap calls are silent.
    await relay.on_otp_required("p5", attempt=5)
    assert len(fake_bot.sent) == sends_at_exhaustion

    relay.reset_exhaustion()

    await relay.on_otp_required("p6", attempt=4)
    assert len(fake_bot.sent) == sends_at_exhaustion + 1
    assert "gave up" in fake_bot.sent[-1].text.lower()


# ---------------------------------------------------------------------------
# Fix B — stale-OTP guidance in every prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_otp_prompt_includes_timestamp_and_stale_warning(
    fake_bot: FakeAdminBot, fake_manager: FakeManager,
) -> None:
    """Each OTP prompt (initial + retry) must call out the UTC timestamp
    and warn the operator to only reply to the latest message. Without
    this, an operator with 5 unread OTP emails has no way to tell which
    code is current — that's how a 03:58 UTC disconnect spiralled into
    five invalidated PINs."""
    relay = _relay(fake_bot, fake_manager)
    await relay.on_otp_required("p", attempt=1)
    assert len(fake_bot.sent) == 1
    initial_text = fake_bot.sent[0].text
    assert "UTC" in initial_text
    assert "⚠️" in initial_text
    assert "latest" in initial_text.lower()

    # Retry path edits the existing message — same suffix must appear.
    await relay.on_otp_required("p", attempt=2)
    assert len(fake_bot.edits) == 1
    retry_text = fake_bot.edits[0].text
    assert "UTC" in retry_text
    assert "⚠️" in retry_text
    assert "latest" in retry_text.lower()
