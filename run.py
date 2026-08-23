"""
run.py — Clean architecture entry point.

Start the server with:
    python run.py
    uvicorn app.main:app --reload

The original main.py is preserved for reference and backward compatibility.
"""
import os
import socket

from app.main import app  # noqa: F401 — triggers app factory


def _is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _pick_server_port(preferred_port: int, host: str = "0.0.0.0", max_tries: int = 20) -> int:
    port = preferred_port
    for _ in range(max_tries):
        if _is_port_available(host, port):
            return port
        port += 1
    raise RuntimeError(f"No free port found near {preferred_port}")


if __name__ == "__main__":
    import uvicorn

    preferred_port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    server_port = _pick_server_port(preferred_port, host=host)

    print("\n" + "=" * 70)
    print("  [START] Coconut Disease Detector — Clean Architecture")
    print("=" * 70)
    print(f"  [WEB]  http://localhost:{server_port}")
    print(f"  [DOCS] http://localhost:{server_port}/docs")
    if server_port != preferred_port:
        print(f"  [PORT] {preferred_port} was busy, using {server_port}")
    print("  [EXIT] Ctrl+C to stop")
    print("=" * 70 + "\n")

    uvicorn.run(app, host=host, port=server_port, log_level="info")
