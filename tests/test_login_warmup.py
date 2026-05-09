"""Regression tests for the Cloudflare-friendly ``Login.get_token`` path.

Cloudflare in front of ``qxbroker.com`` rejects two kinds of requests
that earlier revisions of this code accidentally produced:

1. **Cold deep-link to ``/<lang>/sign-in/modal/``.** A session that has
   never visited a top-level page lacks the ``laravel_session`` /
   ``_cfuvid`` cookies the edge hands out at ``/<lang>/sign-in`` and
   gets a managed JS challenge (HTTP 403, ``cf-mitigated: challenge``,
   "Just a moment..." interstitial) that curl_cffi cannot solve.
2. **Hand-crafted browser-shape headers that disagree with the JA3.**
   ``curl_cffi.AsyncSession(impersonate="chrome120")`` ships its own
   ``Sec-Ch-Ua*``, ``Sec-Fetch-*``, ``Upgrade-Insecure-Requests``,
   ``Accept-Encoding`` and ``User-Agent`` values matching the wire
   fingerprint (a *macOS* Chrome 120 build). Setting ``Sec-Ch-Ua-
   Platform: "Linux"`` or any other Linux-shaped variant on top is the
   exact cross-check Cloudflare's bot manager uses to flag bots.

These tests pin both invariants:

* request order — a single GET to ``/<lang>/sign-in`` precedes the GET
  to ``/<lang>/sign-in/modal/`` in the same session;
* "hands off" headers — none of the JA3-coupled browser-shape headers
  are emitted from this layer (curl_cffi handles them).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pyquotex.network.login import Login


# Headers that MUST NOT be set by Login.get_token — curl_cffi's
# impersonation owns them, and any value we pick here will sooner or
# later disagree with whatever real Chrome is shipping today.
_HANDS_OFF = {
    "Sec-Ch-Ua",
    "Sec-Ch-Ua-Mobile",
    "Sec-Ch-Ua-Platform",
    "Sec-Fetch-Site",
    "Sec-Fetch-User",
    "Sec-Fetch-Dest",
    "Sec-Fetch-Mode",
    "Upgrade-Insecure-Requests",
    "Accept-Encoding",
    "Connection",
    "Dnt",
}


def _make_response(status: int, text: str) -> SimpleNamespace:
    """Build a minimal duck-typed response.

    ``Login.get_token`` only reads ``.content`` (via ``get_soup``) and
    ``.status_code``; the rest is shape that ``Browser.get_soup``
    inspects. ``BeautifulSoup`` accepts an empty body fine.
    """
    return SimpleNamespace(
        status_code=status,
        content=text.encode(),
        text=text,
        headers={},
        url="https://qxbroker.com/en/sign-in/modal/",
        reason_phrase="OK" if status < 400 else "",
        reason="OK" if status < 400 else "",
    )


@pytest.mark.asyncio
async def test_get_token_warms_up_signin_before_modal():
    """Cold-deep-link is the bot tell — warmup must come first, and the
    warmup itself must look like a freshly typed URL (no Referer)."""
    api = SimpleNamespace(lang="en", session_data={})
    login = Login(api)

    modal_html = (
        '<html><body><form>'
        '<input name="_token" value="csrf-abc">'
        '</form></body></html>'
    )
    # ``calls`` records (url, headers-snapshot-at-send-time). We snapshot
    # because ``Login`` mutates ``self.headers`` between requests and we
    # want each call's headers as they were on the wire, not at the end.
    calls: list[tuple[str, dict[str, str]]] = []

    async def fake_request(method: str, url: str, **_: object):
        calls.append((f"{method} {url}", dict(login.headers)))
        if url.endswith("/sign-in/modal/"):
            login.response = _make_response(200, modal_html)
        else:
            login.response = _make_response(200, "<html></html>")
        return login.response

    login.send_request = AsyncMock(side_effect=fake_request)  # type: ignore[method-assign]

    token = await login.get_token()

    assert token == "csrf-abc"

    # 1. Order: warmup first, modal second.
    urls = [c[0] for c in calls]
    assert urls == [
        "GET https://qxbroker.com/en/sign-in",
        "GET https://qxbroker.com/en/sign-in/modal/",
    ], f"Expected warmup-then-modal, got: {urls!r}"

    warmup_headers, modal_headers = calls[0][1], calls[1][1]

    # 2. Warmup is a "freshly typed URL" — no Referer claim.
    assert "Referer" not in warmup_headers, (
        "Warmup must not claim a Referer; we haven't visited any page "
        f"yet. Got: {warmup_headers.get('Referer')!r}"
    )

    # 3. Modal request DOES claim Referer=/sign-in (the warmup we just did).
    assert modal_headers.get("Referer") == "https://qxbroker.com/en/sign-in", (
        "Modal request must claim Referer=/sign-in (the warmup we just "
        f"completed). Got: {modal_headers.get('Referer')!r}"
    )

    # 4. None of the JA3-coupled browser-shape headers are set on either
    #    request — curl_cffi's impersonation handles them.
    for label, hdrs in [("warmup", warmup_headers), ("modal", modal_headers)]:
        leaked = _HANDS_OFF.intersection(hdrs)
        assert not leaked, (
            f"{label} request leaked browser-shape headers that "
            f"curl_cffi must own: {sorted(leaked)!r}. These create a "
            f"JA3-vs-headers mismatch that Cloudflare's bot manager "
            f"flags. Drop them and trust impersonate()."
        )


@pytest.mark.asyncio
async def test_get_token_proceeds_when_warmup_fails():
    """If the warmup itself raises (network blip, intermittent CF),
    we still attempt the modal GET — that's the request whose result
    actually decides the login outcome."""
    api = SimpleNamespace(lang="en", session_data={})
    login = Login(api)

    modal_html = (
        '<html><body>'
        '<input name="_token" value="csrf-after-warmup-fail">'
        '</body></html>'
    )
    calls: list[str] = []

    async def fake_request(method: str, url: str, **_: object):
        calls.append(f"{method} {url}")
        if url.endswith("/sign-in"):
            raise RuntimeError("simulated transient warmup error")
        login.response = _make_response(200, modal_html)
        return login.response

    login.send_request = AsyncMock(side_effect=fake_request)  # type: ignore[method-assign]

    token = await login.get_token()

    assert token == "csrf-after-warmup-fail"
    assert calls == [
        "GET https://qxbroker.com/en/sign-in",
        "GET https://qxbroker.com/en/sign-in/modal/",
    ]


def test_browser_uas_are_in_lockstep_with_curl_cffi_impersonation():
    """``_UA_CHROME`` and ``_UA_FIREFOX`` must claim the same OS that
    curl_cffi's matching impersonate profile actually sends — anything
    else creates a UA-vs-JA3 cross-check failure that Cloudflare flags.

    For curl_cffi 0.15 every profile we use (``chrome120``, ``chrome142``,
    ``firefox144``, ``safari260``) ships a *macOS* fingerprint. If a
    future curl_cffi release changes the OS for these profiles, sync
    these constants + the comment above them.
    """
    from pyquotex.network.navigator import Browser

    chrome_ua = Browser._UA_CHROME
    firefox_ua = Browser._UA_FIREFOX
    for label, ua in [("_UA_CHROME", chrome_ua), ("_UA_FIREFOX", firefox_ua)]:
        assert ("Macintosh" in ua and "Mac OS X" in ua), (
            f"{label} must match curl_cffi's macOS-shaped JA3. "
            f"Got: {ua!r}. Run an `impersonate=` probe against "
            f"https://httpbin.org/headers to see what curl_cffi "
            f"actually sends and align this string."
        )
