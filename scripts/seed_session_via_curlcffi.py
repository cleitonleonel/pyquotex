"""Seed session.json by performing the full login via curl_cffi (TLS impersonation).

The library's normal httpx login fails behind Cloudflare from datacenter
IPs because httpx's TLS fingerprint doesn't match a real Firefox.
``curl_cffi`` does proper JA3 impersonation, so we use it ONCE here just
to obtain the SSID + cookies, then write them to ``session.json`` so the
regular library code can pick up from there using its WebSocket flow.

This script is NOT a runtime dependency of pyquotex — it's a smoke-test
helper. Requires ``pip install curl_cffi`` in the local venv only.

Usage:
    PYTHONPATH=. python scripts/seed_session_via_curlcffi.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from curl_cffi import requests  # local dev dep only

from pyquotex.config import credentials, resource_path

BASE = "https://qxbroker.com"
LANG = "en"
IMPERSONATE = "firefox133"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:127.0) Gecko/20100101 Firefox/127.0"


def _cookies_to_header(jar: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in jar.items())


def _extract_token(html: str) -> str | None:
    m = re.search(
        r'<input[^>]*name=["\']_token["\'][^>]*value=["\']([^"\']+)["\']',
        html,
    )
    return m.group(1) if m else None


def main() -> int:
    email, password = credentials()
    s = requests.Session(impersonate=IMPERSONATE)
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.5"})

    # 1. Warm: pick up __cf_bm + laravel_session
    r = s.get(f"{BASE}/{LANG}")
    print(f"  GET /{LANG} -> {r.status_code} (cookies: {list(s.cookies.keys())})")
    if r.status_code != 200:
        print("  ❌ Cloudflare still blocking — try a different impersonate value")
        return 1

    # 2. Get sign-in modal + CSRF _token
    r = s.get(f"{BASE}/{LANG}/sign-in/modal/")
    print(f"  GET /sign-in/modal/ -> {r.status_code}")
    token = _extract_token(r.text)
    if not token:
        print("  ❌ Could not find _token in modal page")
        return 1
    print(f"  _token: {token[:32]}…")

    # 3. POST credentials — exactly the lib's path: /sign-in/ (trailing slash)
    data = {
        "_token": token,
        "email": email,
        "password": password,
        "remember": 1,
    }
    r = s.post(
        f"{BASE}/{LANG}/sign-in/",
        data=data,
        headers={
            "Referer": f"{BASE}/{LANG}/sign-in",
            "Origin": BASE,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    print(f"  POST /sign-in/ -> {r.status_code} final_url={r.url}")

    if 'name="keep_code"' in r.text:
        print("  ⚠️  2FA challenge required — paste the code:")
        code = input("  > ").strip()
        data["keep_code"] = 1
        data["code"] = code
        r = s.post(
            f"{BASE}/{LANG}/sign-in/modal",
            data=data,
            headers={
                "Referer": f"{BASE}/{LANG}/sign-in/modal",
                "Origin": BASE,
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        print(f"  POST /sign-in/modal -> {r.status_code} final_url={r.url}")
        Path("/tmp/qx_pin_response.html").write_text(r.text)
        print("  (saved response to /tmp/qx_pin_response.html)")
        # Snippet of error if any
        import re as _re

        err = _re.search(
            r'<div[^>]*class="[^"]*error[^"]*"[^>]*>(.*?)</div>',
            r.text,
            _re.S,
        )
        if err:
            print(f"  Error block: {err.group(1).strip()[:200]}")
        if 'name="keep_code"' in r.text:
            print("  ⚠️  Still on PIN form after submitting — code rejected or expired")

    if "/trade" not in str(r.url):
        # If still not on /trade, hit it explicitly
        r = s.get(f"{BASE}/{LANG}/trade")
        print(f"  GET /trade -> {r.status_code}")

    ssid: str | None = None
    m = re.search(r"window\.settings\s*=\s*(\{.*?\});", r.text, re.S)
    if m:
        try:
            settings_data = json.loads(m.group(1))
            ssid = settings_data.get("token")
            if ssid:
                print(f"  SSID via window.settings: {ssid[:24]}…")
        except Exception as e:
            print(f"  ⚠️  window.settings parse failed: {e}")

    cookie_jar = s.cookies.get_dict()

    # Fallback: /api/v1/cabinets/digest (used by Login.get_profile)
    if not ssid:
        r2 = s.get(
            f"{BASE}/api/v1/cabinets/digest",
            headers={"Referer": f"{BASE}/{LANG}/trade"},
        )
        print(f"  GET /api/v1/cabinets/digest -> {r2.status_code}")
        if r2.status_code == 200:
            try:
                ssid = r2.json().get("data", {}).get("token")
                if ssid:
                    print(f"  SSID via /digest: {ssid[:24]}…")
            except Exception as e:
                print(f"  ⚠️  digest parse failed: {e}")

    if not ssid:
        print("  ❌ Could not extract SSID from /trade page")
        print("     Cookies available:", list(cookie_jar.keys()))
        return 2

    cookies_header = _cookies_to_header(cookie_jar)
    out: dict[str, Any] = {}
    session_path = Path(resource_path("session.json"))
    if session_path.exists():
        try:
            out = json.loads(session_path.read_text())
        except Exception:
            pass
    out[email] = {
        "cookies": cookies_header,
        "token": ssid,
        "user_agent": UA,
    }
    session_path.write_text(json.dumps(out, indent=4))
    print(f"\n  ✅ Wrote session.json for {email}")
    print(f"     cookies: {len(cookie_jar)} entries")
    print(f"     ssid: {ssid[:16]}…")
    return 0


if __name__ == "__main__":
    sys.exit(main())
