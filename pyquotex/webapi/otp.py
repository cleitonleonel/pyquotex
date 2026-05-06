"""Bridge between the broker's interactive OTP prompt and the
``POST /auth/otp`` endpoint.

The pyquotex login flow expects a synchronous-or-async callback that
returns the PIN code when the broker emails one (first login from a
new device/IP). Inside a server we can't read from stdin, so this
manager wires the callback to an ``asyncio.Future`` that the
``/auth/otp`` route resolves with the user-supplied code.

Lifecycle:

1. ``Quotex.connect()`` enters the login flow and calls
   :meth:`OtpManager.callback`. We create a fresh Future, expose the
   prompt text via :attr:`prompt`, and await the Future.
2. The web client polls ``GET /health`` (or the original
   ``POST /auth/connect`` returns 202 with ``otp_required: True``)
   and POSTs the code to ``/auth/otp``.
3. :meth:`submit` resolves the Future with the code; the library
   continues the login flow and ``connect()`` eventually completes.
4. If the broker rejects the code as not-digits the library
   recurses, so :meth:`callback` may be invoked multiple times in a
   single connect — each call creates a fresh Future.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_OTP_TIMEOUT = 300.0  # 5 min — broker invalidates the PIN beyond this


class OtpManager:
    """Asyncio-friendly bridge between the broker's OTP callback and an
    HTTP endpoint.

    Thread-/task-safe: a single internal lock serialises the callback
    and submit paths. Multiple concurrent ``connect()`` flows are
    *not* supported (the webapi is single-tenant; only one connect at
    a time).
    """

    def __init__(self, timeout: float = DEFAULT_OTP_TIMEOUT) -> None:
        self.timeout = timeout
        self._lock = asyncio.Lock()
        self._future: asyncio.Future[str] | None = None
        self._prompt: str | None = None

    # ----- broker-side ----------------------------------------------------

    async def callback(self, prompt: str) -> str:
        """Invoked from inside the broker login flow.

        Blocks until :meth:`submit` is called by the ``/auth/otp``
        route or :attr:`timeout` seconds elapse.
        """
        loop = asyncio.get_running_loop()
        async with self._lock:
            # Cancel any stale future from a previous attempt that
            # never resolved (shouldn't happen in normal flow but is
            # defensible — e.g. the previous /auth/connect was
            # abandoned by the client).
            if self._future is not None and not self._future.done():
                self._future.cancel()
            self._future = loop.create_future()
            self._prompt = prompt or "OTP required"
            future = self._future
            logger.info("OTP callback armed: %s", self._prompt)

        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError:
            logger.warning("OTP wait timed out after %.1fs", self.timeout)
            raise
        finally:
            async with self._lock:
                # Clear state only if THIS particular future is still
                # the active one — a re-prompt may have replaced it.
                if self._future is future:
                    self._future = None
                    self._prompt = None

    # ----- HTTP-side ------------------------------------------------------

    @property
    def is_waiting(self) -> bool:
        return self._future is not None and not self._future.done()

    @property
    def prompt(self) -> Optional[str]:
        """Human-readable prompt forwarded from the broker (may be in
        the broker's UI language)."""
        return self._prompt

    def submit(self, code: str) -> bool:
        """Resolve the active callback Future with ``code``.

        Returns ``True`` if a Future was actually waiting (and got
        resolved), ``False`` otherwise.
        """
        if self._future is None or self._future.done():
            return False
        try:
            self._future.set_result(code)
            return True
        except asyncio.InvalidStateError:
            return False

    def reset(self) -> None:
        """Cancel any pending wait. Safe to call when no flow is
        active. Used between connect attempts."""
        if self._future is not None and not self._future.done():
            self._future.cancel()
        self._future = None
        self._prompt = None
