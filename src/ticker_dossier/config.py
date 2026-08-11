"""Local configuration helpers.

Secrets live in environment variables or ignored .env files. This module never
contains real credentials.
"""
from __future__ import annotations

import os
from pathlib import Path


def load_local_env(paths: tuple[str, ...] = (".env.local", ".env")) -> None:
    """Load KEY=VALUE lines from ignored local env files if not already set."""
    root = Path.cwd()
    for name in paths:
        path = root / name
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
