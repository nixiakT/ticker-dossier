"""Enforce explicit complexity ceilings that ratchet down legacy hotspots."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
CHECKS = (
    ("src/ticker_dossier/portfolio/scoring.py", 39),
    ("src/ticker_dossier/research/analysis/quality.py", 31),
)


def main() -> int:
    for relative_path, ceiling in CHECKS:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--isolated",
                "--select",
                "C901",
                "--config",
                f"lint.mccabe.max-complexity={ceiling}",
                relative_path,
            ],
            cwd=ROOT,
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
