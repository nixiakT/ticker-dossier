"""Smoke-test an installed wheel without importing from the checkout."""

from __future__ import annotations

from importlib.metadata import entry_points, version
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
from tempfile import TemporaryDirectory

from ticker_dossier.resources import bundled_mcp_config
from ticker_dossier.skills.loader import load_skills


def _console_script(name: str) -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return Path(sysconfig.get_path("scripts")) / f"{name}{suffix}"


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def main() -> int:
    names = {skill.name for skill in load_skills()}
    assert "finance-stock-research" in names
    assert "trace2skill" in names
    assert bundled_mcp_config().is_file()

    scripts = {item.name for item in entry_points(group="console_scripts")}
    assert {"ticker-dossier", "finance-agent"} <= scripts

    with TemporaryDirectory(prefix="ticker-dossier-smoke-") as temp_dir:
        isolated_home = Path(temp_dir).resolve()
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(isolated_home),
                "USERPROFILE": str(isolated_home),
                "FINANCE_PORTFOLIO_DIR": str(isolated_home / "portfolios"),
                "NO_COLOR": "1",
            }
        )
        _run([sys.executable, "-m", "ticker_dossier", "--help"], cwd=isolated_home, env=env)
        _run([str(_console_script("ticker-dossier")), "--dashboard"], cwd=isolated_home, env=env)
        _run([str(_console_script("finance-agent")), "--selfcheck"], cwd=isolated_home, env=env)
        _run([sys.executable, "-m", "pip", "check"], cwd=isolated_home, env=env)

    print(f"ticker-dossier {version('ticker-dossier')}: {len(names)} bundled Skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
