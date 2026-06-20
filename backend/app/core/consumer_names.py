from __future__ import annotations

import os
import secrets
import socket


def make_consumer_name(prefix: str) -> str:
    """Return a process-unique Redis stream consumer name."""
    hostname = socket.gethostname().split(".")[0] or "host"
    return f"{prefix}-{hostname}-{os.getpid()}-{secrets.token_hex(3)}"
