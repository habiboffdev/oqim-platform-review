"""Ensure the backend app package is importable from CLI command impls."""
from __future__ import annotations

import sys

from cli.config import BACKEND_DIR


def ensure_backend_path() -> None:
    backend = str(BACKEND_DIR)
    if backend not in sys.path:
        sys.path.insert(0, backend)
