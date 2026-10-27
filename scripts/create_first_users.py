r"""Create the first web app users with one-time temporary passwords. RUN BY A HUMAN once.

    .venv\Scripts\python.exe scripts\create_first_users.py

Makes viewer (Viewer), hrm (HRM), admin (Admin / HR) and master000 (master login).
Each user must choose their own password at first sign-in. The temporary passwords are
written ONLY to 07_LOGS\webapp\FIRST_LOGIN.txt (hand them out, then delete that file) and
are never printed. Refuses to run if any of these users already exists.

Created 2026-10-18, version 1. Supersedes: none.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.webapp import auth  # noqa: E402

USERS = [("viewer", "viewer", "Viewer"), ("hrm", "hrm", "HRM"),
         ("admin", "admin", "Admin / HR"), ("master000", "master", "Master Admin")]
ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def temp_password() -> str:
    return "-".join("".join(secrets.choice(ALPHABET) for _ in range(5)) for _ in range(3))


def main() -> int:
    import src.daily as daily
    path = auth.users_path(daily.LOGS_DIR)
    existing = auth.load_users(path)
    clash = [u for u, _, _ in USERS if u in existing]
    if clash:
        print("Already exist, nothing changed:", ", ".join(clash))
        return 1
    lines = ["HR Attendance web app: first sign-in details", "",
             "Document version: 1 | Created: 2026-10-18 | Supersedes: none", "",
             "Each person signs in once with the temporary password and is asked to choose",
             "their own. Hand these out privately, then DELETE this file.", ""]
    for username, role, label in USERS:
        pw = temp_password()
        auth.save_user(path, username, role, pw, must_change=True)
        lines.append(f"{label:28} username: {username:10} temporary password: {pw}")
    (path.parent / "FIRST_LOGIN.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Created {len(USERS)} users. Details are in {path.parent / 'FIRST_LOGIN.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
