"""Step 1/2 — fire credentials POST so Quotex emails a fresh 2FA code,
then persist (as JSON) the cookies + CSRF token for step 2 to resume.

After this runs successfully you'll see a new code in valejoapps@gmail.com.
Hand that code to step 2.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from curl_cffi import requests

from pyquotex.config import credentials

BASE = "https://qxbroker.com"
LANG = "en"
IMPERSONATE = "firefox133"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:127.0) Gecko/20100101 Firefox/127.0"
STATE_PATH = Path("/tmp/qx_seed_state.json")


def main() -> int:
    email, password = credentials()
    s = requests.Session(impersonate=IMPERSONATE)
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.5"})

    r = s.get(f"{BASE}/{LANG}")
    print(f"  GET /{LANG} -> {r.status_code}")
    if r.status_code != 200:
        return 1

    r = s.get(f"{BASE}/{LANG}/sign-in/modal/")
    print(f"  GET /sign-in/modal/ -> {r.status_code}")
    m = re.search(
        r'<input[^>]*name=["\']_token["\'][^>]*value=["\']([^"\']+)["\']',
        r.text,
    )
    if not m:
        print("  ❌ No _token in modal")
        return 1
    token = m.group(1)

    r = s.post(
        f"{BASE}/{LANG}/sign-in/",
        data={
            "_token": token,
            "email": email,
            "password": password,
            "remember": 1,
        },
        headers={
            "Referer": f"{BASE}/{LANG}/sign-in",
            "Origin": BASE,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    print(f"  POST /sign-in/ -> {r.status_code}")
    if 'name="keep_code"' not in r.text:
        print("  ⚠️  No 2FA form returned — login may have already passed or failed")
        Path("/tmp/qx_step1_response.html").write_text(r.text)
        return 2

    STATE_PATH.write_text(
        json.dumps(
            {
                "cookies": s.cookies.get_dict(),
                "token": token,
                "email": email,
            },
            indent=2,
        )
    )
    print(f"  ✅ State saved to {STATE_PATH}")
    print("     → CHECK valejoapps@gmail.com for the LATEST 6-digit code,")
    print("       then run scripts/seed_session_step2.py with that code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
