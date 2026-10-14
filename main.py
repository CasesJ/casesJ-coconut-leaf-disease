"""Coconut Disease Detector application launcher.

Run the system from the repository root with::

    python main.py
"""
import os
import socket

import uvicorn

from app.main import app


def _is_port_available(host: str, port: int) -> bool:
    """Return whether *port* can be bound on *host*."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _pick_server_port(preferred_port: int, host: str, max_tries: int = 20) -> int:
    """Use the requested port, or the next available one."""
    for port in range(preferred_port, preferred_port + max_tries):
        if _is_port_available(host, port):
            return port
    raise RuntimeError(f"No free port found between {preferred_port} and {preferred_port + max_tries - 1}")


def main() -> None:
    """Start the Coconut Disease Detector web server."""
    preferred_port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    port = _pick_server_port(preferred_port, host)

    print("\n" + "=" * 70)
    print("  Coconut Disease Detector")
    print("=" * 70)
    print(f"  Open: http://localhost:{port}")
    print(f"  Docs: http://localhost:{port}/docs")
    if port != preferred_port:
        print(f"  Port {preferred_port} is busy; using {port}.")
    print("  Press Ctrl+C to stop.")
    print("=" * 70 + "\n")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
