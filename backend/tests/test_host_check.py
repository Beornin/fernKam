"""The Host guard: this machine's own names pass, a rebound foreign domain does not.

Run: uv run python tests/test_host_check.py
"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fernkam.api.app import _host_allowed


def main() -> None:
    me = socket.gethostname()
    for ok in ("127.0.0.1:8000", "localhost:8000", "localhost", "[::1]:8000", "192.168.1.20:8000", f"{me}:8000",
               "app.localhost:8000", ""):
        assert _host_allowed(ok), ok
    for bad in ("evil.example.com", "evil.example.com:8000", "127.0.0.1.evil.com:8000", "attacker.net"):
        assert not _host_allowed(bad), bad
    print("ok - own names pass; a foreign domain (DNS rebinding) is refused")


if __name__ == "__main__":
    main()
