import os
import sys
from pathlib import Path

_BOOTSTRAP_ENV = "OQIM_CLI_BOOTSTRAPPED"


def _find_repo_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (
            (candidate / "cli").is_dir()
            and (candidate / "backend").is_dir()
            and (candidate / "pyproject.toml").is_file()
        ):
            return candidate
    return None


def _preferred_runtime(project_root: Path) -> Path | None:
    candidates = (
        project_root / "backend" / ".venv" / "bin" / "python",
        project_root / "backend" / "venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _bootstrap_repo_runtime() -> None:
    if os.getenv(_BOOTSTRAP_ENV) == "1":
        return

    project_root = _find_repo_root(Path.cwd().resolve())
    if project_root is None:
        return

    target_python = _preferred_runtime(project_root)
    if target_python is None:
        return

    current_python = Path(sys.executable).absolute()
    if current_python == target_python.absolute():
        return

    env = os.environ.copy()
    env[_BOOTSTRAP_ENV] = "1"

    existing_pythonpath = env.get("PYTHONPATH", "")
    path_parts = [part for part in existing_pythonpath.split(os.pathsep) if part]
    project_root_str = str(project_root)
    if project_root_str not in path_parts:
        path_parts.insert(0, project_root_str)
    env["PYTHONPATH"] = os.pathsep.join(path_parts)

    os.execve(
        str(target_python),
        [str(target_python), "-m", "cli.__main__", *sys.argv[1:]],
        env,
    )


_bootstrap_repo_runtime()

from cli.app import app


def main():
    app()


if __name__ == "__main__":
    main()
