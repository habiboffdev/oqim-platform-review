"""Verify the packaged Hermes runtime is importable.

Hermes is installed as a pinned dependency from the OQIM fork. This module is a
small compatibility boundary for OQIM's adapter code; it intentionally does not
insert ``backend/vendor/hermes`` into ``sys.path``.
"""
from __future__ import annotations

import sys
from importlib.util import find_spec
from pathlib import Path

_OLD_VENDOR_ROOT = Path(__file__).resolve().parents[4] / "vendor" / "hermes"
_OQIM_HERMES_PACKAGE_PIN = (
    "hermes-agent @ git+https://github.com/habiboffdev/hermes-agent.git"
    "@6abdd98cef003d9875e3cf3b695d31e1cfa5abf1"
)


def ensure_hermes_runtime() -> str:
    vendor_root = str(_OLD_VENDOR_ROOT.resolve())
    sys.path[:] = [
        entry
        for entry in sys.path
        if str(Path(entry).resolve()) != vendor_root
    ]

    spec = find_spec("run_agent")
    if spec is None or spec.origin is None:
        raise RuntimeError(
            "Hermes runtime is not importable. Install the pinned OQIM fork "
            f"dependency: {_OQIM_HERMES_PACKAGE_PIN}"
        )

    origin = Path(spec.origin).resolve()
    if (
        origin == _OLD_VENDOR_ROOT.resolve() / "run_agent.py"
        or _OLD_VENDOR_ROOT.resolve() in origin.parents
    ):
        raise RuntimeError(
            "Hermes resolved from backend/vendor/hermes; Phase 1 requires the "
            "pinned package dependency from the OQIM fork."
        )

    return str(origin.parent)
