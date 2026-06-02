"""Run Lunar Base as a module: ``python -m web``.

Resolves the bind address from web.config (auto-detected LAN IP by default,
overridable via LUNAR_BASE_HOST / LUNAR_BASE_PORT), prints a short banner with
the URL other devices should use, then starts uvicorn.
"""

from __future__ import annotations

import uvicorn

from web import config


def main() -> None:
    host = config.HOST
    port = config.PORT

    print()
    print("=== Lunar Base ===")
    if host == "0.0.0.0":
        # Couldn't pin a single LAN IP (or the user asked for all interfaces).
        print(f"Listening on all network interfaces, port {port}.")
        print(f"  On this PC:      http://127.0.0.1:{port}")
        print(f"  From another PC: http://<this-PC-LAN-IP>:{port}")
    elif host == "127.0.0.1":
        print(f"Open http://127.0.0.1:{port} in your browser (this PC only).")
    else:
        print(f"Reachable on your network at:  http://{host}:{port}")
        print("  (use that address from this PC and from other devices)")
    print("WARNING: no login — anyone who can reach this PC on the network can edit the save.")
    print("Ctrl+C to stop.")
    print()

    uvicorn.run("web.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
