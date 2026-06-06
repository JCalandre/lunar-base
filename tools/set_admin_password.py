"""Create or reset the Lunar Base admin account.

Writes a bcrypt-hashed credential to data/admin.json. This account is local to
Lunar Base and is never written into lunar-tear's auth.db; admin can reach
every user record while game accounts are scoped to their own.

Usage:
    python tools/set_admin_password.py
    python tools/set_admin_password.py --username admin
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt  # noqa: E402

from web import config  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Set the Lunar Base admin password.")
    parser.add_argument("--username", default=None, help="Admin username (default: prompt, 'admin')")
    args = parser.parse_args()

    username = args.username
    if not username:
        username = input("Admin username [admin]: ").strip() or "admin"

    password = getpass.getpass("Admin password: ")
    if not password:
        print("Password cannot be empty.", file=sys.stderr)
        return 1
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        return 1

    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.ADMIN_CONFIG_PATH.write_text(
        json.dumps({"username": username, "password_hash": password_hash}, indent=2),
        encoding="utf-8",
    )
    print(f"Admin account '{username}' written to {config.ADMIN_CONFIG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
