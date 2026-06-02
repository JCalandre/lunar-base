"""Constants and paths for Lunar Base.

All paths resolve relative to the lunar-base/ root, so the app works the same
no matter what cwd it is launched from.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent

def _has_bin(d: Path) -> bool:
    rel = d / "server" / "assets" / "release"
    return rel.is_dir() and any(rel.glob("*.bin.e"))


def _resolve_lunar_tear_dir() -> Path:
    """Find the lunar-tear checkout that actually runs the server.

    Priority: the LUNAR_TEAR_DIR env var (honored as-is, even if empty, so its
    error is clear) > a sibling `lunar-tear/` that holds a bin > whichever
    sibling folder holds the most recently modified master-data bin (so a
    differently-named clone like `lt-upstream/` is picked up automatically) >
    the plain `lunar-tear/` default for a sensible "not found" message.
    """
    env = os.environ.get("LUNAR_TEAR_DIR")
    if env:
        return Path(env).resolve()
    default = (ROOT.parent / "lunar-tear").resolve()
    if _has_bin(default):
        return default
    best: Path | None = None
    best_mtime = -1.0
    try:
        for d in ROOT.parent.iterdir():
            if not d.is_dir() or not _has_bin(d):
                continue
            mtime = max(p.stat().st_mtime for p in (d / "server" / "assets" / "release").glob("*.bin.e"))
            if mtime > best_mtime:
                best, best_mtime = d.resolve(), mtime
    except OSError:
        pass
    return best or default


# Defaults to whichever sibling checkout holds the live master-data bin (so the
# editors patch the bin the running server reads); override with LUNAR_TEAR_DIR.
LUNAR_TEAR_DIR: Path = _resolve_lunar_tear_dir()
GAME_DB_PATH: Path = (LUNAR_TEAR_DIR / "server" / "db" / "game.db").resolve()
WIZARD_CONFIG_PATH: Path = (LUNAR_TEAR_DIR / "server" / ".wizard.json").resolve()

DATA_DIR: Path = ROOT / "data"
BACKUP_DIR: Path = DATA_DIR / "backups"
MASTERDATA_DIR: Path = DATA_DIR / "masterdata"
NAMES_DIR: Path = DATA_DIR / "names"

# Go produces a `.exe` on Windows and an extensionless binary elsewhere. The
# setup scripts build whichever is appropriate for the host, so resolve the
# matching name here.
_GRANT_EXE_NAME: str = "grant.exe" if sys.platform == "win32" else "grant"
GRANT_EXE_PATH: Path = ROOT / "tools" / "grant" / _GRANT_EXE_NAME

# Name of the setup helper for the host OS, used in user-facing error messages.
SETUP_SCRIPT: str = "setup.bat" if sys.platform == "win32" else "setup.sh"


def find_master_data_bin() -> Path | None:
    """Locate the encrypted master-data binary inside lunar-tear.

    The filename embeds a build timestamp and changes whenever the game data is
    repatched, so we glob for `*.bin.e` and take the most recently modified.
    Returns None if the file is missing — callers should surface that as a
    user-actionable error.
    """
    release_dir = LUNAR_TEAR_DIR / "server" / "assets" / "release"
    if not release_dir.is_dir():
        return None
    candidates = sorted(release_dir.glob("*.bin.e"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


BACKUP_RETENTION: int = 50

def detect_lan_ip() -> str | None:
    """Best-effort detection of this machine's primary LAN IPv4 address.

    Opens a UDP socket toward a public address and reads back the local end of
    the route the OS would use — no packets are actually sent, and it works
    offline as long as a default route/interface exists. Returns None if it
    can't determine a real (non-loopback) address.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    return None if ip.startswith("127.") else ip


def _resolve_host() -> str:
    """Pick the bind address.

    Precedence:
      1. LUNAR_BASE_HOST env var, if set (e.g. 0.0.0.0 or 127.0.0.1).
      2. The auto-detected LAN IP, so the app is reachable from other PCs on the
         network and is NOT served on 127.0.0.1.
      3. 0.0.0.0 as a fallback if detection fails, so the server still starts.
    """
    override = os.environ.get("LUNAR_BASE_HOST")
    if override:
        return override
    return detect_lan_ip() or "0.0.0.0"


# Bind address and port. By default Lunar Base binds to this machine's detected
# LAN IP so it is reachable from other PCs on the network (and 127.0.0.1 is NOT
# served). NOTE: there is no auth — anyone who can reach this PC on the network
# can edit the game database, so only run it on a network you trust.
#   - Set LUNAR_BASE_HOST=0.0.0.0   to bind every interface (incl. 127.0.0.1).
#   - Set LUNAR_BASE_HOST=127.0.0.1 to restrict to this PC only.
HOST: str = _resolve_host()
PORT: int = int(os.environ.get("LUNAR_BASE_PORT", "8888"))

LUNAR_TEAR_DEFAULT_GRPC_PORT: int = 8003
