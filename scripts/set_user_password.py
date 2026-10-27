r"""Set a Client web app password. RUN BY A HUMAN in a terminal.

    .venv\Scripts\python.exe scripts\set_user_password.py

Prompts (getpass, twice) and stores a salted scrypt hash in
07_LOGS\webapp\users.json. The password is never printed or stored.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.webapp import auth  # noqa: E402

MIN_LEN = 10


def main() -> int:
    role = input(f"Role {auth.ROLES}: ").strip().lower()
    if role not in auth.ROLES:
        print("Unknown role.")
        return 1
    username = input(f"Username [{role}]: ").strip().lower() or role
    pw1 = getpass.getpass("New password: ")
    pw2 = getpass.getpass("Repeat password: ")
    if pw1 != pw2:
        print("Passwords do not match. Nothing saved.")
        return 1
    if len(pw1) < MIN_LEN:
        print(f"Use at least {MIN_LEN} characters. Nothing saved.")
        return 1
    import src.daily as daily
    path = auth.users_path(daily.LOGS_DIR)
    auth.save_user(path, username, role, pw1)
    print(f"Saved {role} user '{username}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
