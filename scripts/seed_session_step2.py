"""Step 2/2 — submit the freshly-arrived PIN code using state from step 1.

Usage:
    PYTHONPATH=. python scripts/seed_session_step2.py <CODE>
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from curl_cffi import requests

from pyquotex.config import credentials, resource_path

BASE = "https://qxbroker.com"
LANG = "en"
IMPERSONATE = "firefox133"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:127.0) Gecko/20100101 Firefox/127.0"
STATE_PATH = Path("/tmp/qx_seed_state.json")


def _cookies_to_header(jar: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in jar.items())


def main(code: str) -> int:
    if not STATE_PATH.exists():
        print("  ❌ Run scripts/seed_session_step1.py first.")
        return 1

    state = json.loads(STATE_PATH.read_text())
    email = state["email"]
    cookies = state["cookies"]
    token = state["token"]

    s = requests.Session(impersonate=IMPERSONATE)
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.5"})
    for k, v in cookies.items():
        s.cookies.set(k, v, domain=".qxbroker.com")

    pwd = credentials()[1]
    r = s.post(
        f"{BASE}/{LANG}/sign-in/modal",
        data={
            "_token": token,
            "email": email,
            "password": pwd,
            "remember": 1,
            "keep_code": 1,
            "code": code,
        },
        headers={
            "Referer": f"{BASE}/{LANG}/sign-in/modal",
            "Origin": BASE,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    print(f"  POST /sign-in/modal (with code) -> {r.status_code} final_url={r.url}")
    if 'name="keep_code"' in r.text:
        Path("/tmp/qx_step2_response.html").write_text(r.text)
        m = re.search(
            r'<div[^>]*class="[^"]*error[^"]*"[^>]*>(.*?)</div>',
            r.text,
            re.S,
        )
        if m:
            print(f"  Error: {m.group(1).strip()[:200]}")
        return 2

    # We should now be at /trade
    if "/trade" not in str(r.url):
        r = s.get(f"{BASE}/{LANG}/trade")
        print(f"  GET /trade -> {r.status_code}")

    ssid: str | None = None
    m = re.search(r"window\.settings\s*=\s*(\{.*?\});", r.text, re.S)
    if m:
        try:
            data_settings = json.loads(m.group(1))
            ssid = data_settings.get("token")
        except Exception as e:
            print(f"  window.settings parse failed: {e}")
    if not ssid:
        r2 = s.get(
            f"{BASE}/api/v1/cabinets/digest",
            headers={"Referer": f"{BASE}/{LANG}/trade"},
        )
        print(f"  GET /api/v1/cabinets/digest -> {r2.status_code}")
        if r2.status_code == 200:
            try:
                ssid = r2.json().get("data", {}).get("token")
            except Exception:
                pass

    if not ssid:
        print("  ❌ Login passed but SSID not found.")
        Path("/tmp/qx_step2_trade.html").write_text(r.text)
        return 3

    print(f"  ✅ SSID: {ssid[:24]}…")

    cookie_jar = s.cookies.get_dict()
    session_path = Path(resource_path("session.json"))
    out: dict[str, Any] = {}
    if session_path.exists():
        try:
            out = json.loads(session_path.read_text())
        except Exception:
            pass
    out[email] = {
        "cookies": _cookies_to_header(cookie_jar),
        "token": ssid,
        "user_agent": UA,
    }
    session_path.write_text(json.dumps(out, indent=4))
    print(f"  ✅ Wrote session.json ({len(cookie_jar)} cookies)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: seed_session_step2.py <CODE>")
        sys.exit(1)
    sys.exit(main(sys.argv[1].strip()))
