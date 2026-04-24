import configparser
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from fake_useragent import UserAgent

USER_AGENT = (
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) "
    "Gecko/20100101 Firefox/119.0"
)

base_dir = Path.cwd()
config_path = Path(os.path.join(base_dir, "settings/config.ini"))
config = configparser.ConfigParser(interpolation=None)

session_lock = threading.Lock()


def credentials() -> tuple[str, str]:
    """Get or prompt for user credentials from config file."""
    if not config_path.exists():
        config_path.parent.mkdir(exist_ok=True, parents=True)
        text_settings = (
            f"[settings]\n"
            f"email={input('Enter your account email: ')}\n"
            f"password={input('Enter your account password: ')}\n"
        )
        config_path.write_text(text_settings)

    config.read(config_path, encoding="utf-8")

    email = config.get("settings", "email")
    password = config.get("settings", "password")

    if not email or not password:
        print("Email and password cannot be left blank...")
        sys.exit()

    return email, password


def resource_path(relative_path: str | Path) -> Path:
    """Get absolute path to resource, works for dev and for PyInstaller"""
    global base_dir
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_dir = Path(sys._MEIPASS)
    return base_dir / relative_path


def load_session(email: str, user_agent: str | None = None) -> dict[str, Any]:
    """Load session data for a specific email."""
    if user_agent is None:
        user_agent = UserAgent().random
        
    output_file = Path(resource_path("session.json"))
    with session_lock:
        all_sessions = {}
        if output_file.exists():
            try:
                all_sessions = json.loads(output_file.read_text())
            except json.JSONDecodeError:
                pass
        else:
            output_file.parent.mkdir(exist_ok=True, parents=True)

        if email not in all_sessions:
            all_sessions[email] = {
                "cookies": None,
                "token": None,
                "user_agent": user_agent
            }
            output_file.write_text(json.dumps(all_sessions, indent=4))
        
        return all_sessions.get(email)


def update_session(email: str, d: dict[str, Any]) -> dict[str, Any]:
    """Update and persist session data for a specific email."""
    output_file = Path(resource_path("session.json"))
    with session_lock:
        current_sessions = {}
        if output_file.exists():
            try:
                current_sessions = json.loads(output_file.read_text())
            except json.JSONDecodeError:
                pass
        else:
            output_file.parent.mkdir(exist_ok=True, parents=True)

        current_sessions[email] = d
        output_file.write_text(json.dumps(current_sessions, indent=4))
        return current_sessions.get(email)
