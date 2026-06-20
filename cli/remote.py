"""Run an oqim command on the prod VM over SSH (`--prod`).

`--prod` only relocates WHERE a command runs; mutating commands keep their own
guards (e.g. `ai compact` defaults to dry-run). The VM-side `oqim` wrapper
(`scripts/oqim`) sources `/etc/oqim/oqim.env` and cds to the repo root, so the
remote output is clean (no env/locale/cwd noise)."""
from __future__ import annotations

import os
import shlex
import subprocess

_DEFAULT_PROD_SSH = "user@YOUR_VM_HOST"


def remote_argv(argv: list[str]) -> list[str]:
    """The CLI argv with the first `--prod` flag removed (forwarded to the VM)."""
    try:
        idx = argv.index("--prod")
        return argv[:idx] + argv[idx + 1:]
    except ValueError:
        return list(argv)


def bridge_to_prod(argv: list[str]) -> int:
    """SSH to the prod VM and run `oqim <argv>` there, streaming output back.

    Returns the remote exit code. `argv` must already have `--prod` stripped.
    Each forwarded token is shlex-quoted so the remote shell treats it as a
    literal (no metacharacter reinterpretation / arg-splitting)."""
    host = os.environ.get("OQIM_PROD_SSH", _DEFAULT_PROD_SSH)
    remote_cmd = "oqim " + " ".join(shlex.quote(a) for a in argv)
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        host,
        remote_cmd,
    ]
    result = subprocess.run(cmd)
    return int(result.returncode)
