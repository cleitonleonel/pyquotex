import os
import sys
import json
import configparser
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0"

base_dir = Path.cwd()
config_path = Path(os.path.join(base_dir, "settings/config.ini"))
config = configparser.ConfigParser(interpolation=None)


def credentials():

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
    global base_dir
    """Get absolute path to resource, works for dev and for PyInstaller"""
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        base_dir = Path(sys._MEIPASS)
    return base_dir / relative_path


def load_session(user_agent):
    output_file = Path(
        resource_path(
            "session.json"
        )
    )
    if os.path.isfile(output_file):
        with open(output_file) as file:
            session_data = json.loads(
                file.read()
            )
    else:
        output_file.parent.mkdir(
            exist_ok=True,
            parents=True
        )
        session_dict = {
            "cookies": None,
            "token": None,
            "user_agent": user_agent
        }
        session_result = json.dumps(session_dict, indent=4)
        output_file.write_text(
            session_result
        )
        session_data = json.loads(
            session_result
        )
    return session_data


def update_session(session_data):
    output_file = Path(
        resource_path(
            "session.json"
        )
    )
    session_result = json.dumps(session_data, indent=4)
    output_file.write_text(
        session_result
    )
    session_data = json.loads(
        session_result
    )
    return session_data
