"""MCP project configuration, built-in trust, and process environment policy."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ticker_dossier.resources import bundled_mcp_config

DEFAULT_TIMEOUT = 10.0
MCP_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
MCP_ARGUMENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
BUILTIN_PROJECT_MCP_MODULES = {
    "ticker_dossier.integrations.mcp.echo_server",
    "ticker_dossier.integrations.mcp.finance_server",
}
TRUSTED_PROJECT_MCP_ENV = "TICKER_DOSSIER_TRUSTED_MCP_SERVERS"
LEGACY_TRUSTED_PROJECT_MCP_ENV = "MINI_OPENCLAW_TRUSTED_MCP_SERVERS"
MCP_INHERITED_ENV = {
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP",
    "SYSTEMROOT", "WINDIR", "PATHEXT", "VIRTUAL_ENV", "CONDA_PREFIX",
    "PYTHONUNBUFFERED",
}


class MCPConfigError(RuntimeError):
    """Raised when project MCP configuration is invalid."""


@dataclass(frozen=True)
class MCPServerConfig:
    """Validated process configuration without transport side effects."""

    name: str
    command: tuple[str, ...]
    env: dict[str, str]
    cwd: Path | None
    timeout: float


def _default_mcp_config_path() -> Path:
    project_config = Path.cwd() / ".mcp.json"
    if project_config.is_file():
        return project_config
    resource = bundled_mcp_config()
    return resource if isinstance(resource, Path) else Path(str(resource))


def _mcp_process_env(explicit: dict[str, str] | None = None) -> dict[str, str]:
    process_env = {
        key: value
        for key, value in os.environ.items()
        if key in MCP_INHERITED_ENV
    }
    process_env.update(explicit or {})
    return process_env


def _load_project_configs(
    path: Path,
) -> tuple[list[MCPServerConfig], list[dict[str, str]]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MCPConfigError(f"{path}: unable to read MCP config: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("mcpServers"), dict):
        raise MCPConfigError(f"{path}: expected an object with an 'mcpServers' object")

    configs: list[MCPServerConfig] = []
    errors: list[dict[str, str]] = []
    configured_trust = os.environ.get(TRUSTED_PROJECT_MCP_ENV)
    if configured_trust is None:
        configured_trust = os.environ.get(LEGACY_TRUSTED_PROJECT_MCP_ENV, "")
    trust_tokens = {
        token.strip()
        for token in configured_trust.split(",")
        if token.strip()
    }
    for name, spec in sorted(raw["mcpServers"].items()):
        try:
            configs.append(
                _config_from_spec(
                    name,
                    spec,
                    path.parent,
                    trust_tokens=trust_tokens,
                )
            )
        except Exception as exc:
            errors.append({"name": str(name), "status": "error", "detail": str(exc)})
    return configs, errors


def _config_from_spec(
    name: Any,
    spec: Any,
    root: Path,
    *,
    trust_tokens: set[str] | None = None,
) -> MCPServerConfig:
    if not isinstance(name, str):
        raise MCPConfigError("MCP server name must be a string")
    _validate_mcp_name(name, "server")
    if not isinstance(spec, dict):
        raise MCPConfigError(f"MCP server '{name}' config must be an object")
    transport = spec.get("type", spec.get("transport", "stdio"))
    if transport != "stdio":
        raise MCPConfigError(
            f"MCP server '{name}' uses unsupported transport '{transport}'; only stdio is supported"
        )
    command = spec.get("command")
    args = spec.get("args", [])
    env = spec.get("env", {})
    if not isinstance(command, str) or not command:
        raise MCPConfigError(f"MCP server '{name}' requires a non-empty string command")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise MCPConfigError(f"MCP server '{name}' args must be a list of strings")
    if not isinstance(env, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env.items()
    ):
        raise MCPConfigError(f"MCP server '{name}' env must contain string keys and values")
    timeout = spec.get("timeoutSeconds", DEFAULT_TIMEOUT)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise MCPConfigError(f"MCP server '{name}' timeoutSeconds must be greater than zero")
    cwd = spec.get("cwd")
    if cwd is not None and not isinstance(cwd, str):
        raise MCPConfigError(f"MCP server '{name}' cwd must be a string")
    resolved_cwd: Path | None = None
    if cwd:
        resolved_cwd = Path(cwd)
        if not resolved_cwd.is_absolute():
            resolved_cwd = root / resolved_cwd
        resolved_cwd = resolved_cwd.resolve()
    builtin = _is_builtin_project_server(command, args, env, resolved_cwd, root)
    trust_token = _mcp_trust_token(
        name,
        [command, *args],
        env,
        resolved_cwd or root.resolve(),
        float(timeout),
    )
    if not builtin and trust_token not in (trust_tokens or set()):
        raise MCPConfigError(
            f"MCP server '{name}' is not a trusted built-in; review it, then add its name to "
            f"{TRUSTED_PROJECT_MCP_ENV} as {trust_token} before startup"
        )
    if builtin:
        command = sys.executable
        env = {**env, **_builtin_mcp_env()}
    return MCPServerConfig(
        name=name,
        command=(command, *args),
        env=env,
        cwd=resolved_cwd,
        timeout=float(timeout),
    )


def _package_import_root() -> str:
    """Return the trusted checkout/site-packages root for bundled MCP subprocesses."""
    return str(Path(__file__).resolve().parents[3])


def _builtin_mcp_env() -> dict[str, str]:
    """Keep bundled Python MCP servers importable and UTF-8 on every OS."""
    return {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": _package_import_root(),
    }


def _is_builtin_project_server(
    command: str,
    args: list[str],
    env: dict[str, str],
    cwd: Path | None,
    root: Path,
) -> bool:
    if Path(command).name not in {"python", "python3", Path(sys.executable).name}:
        return False
    if len(args) != 2 or args[0] != "-m" or args[1] not in BUILTIN_PROJECT_MCP_MODULES:
        return False
    if env:
        return False
    effective_cwd = (cwd or root).resolve()
    if effective_cwd != root.resolve():
        return False
    return True


def _mcp_trust_token(
    name: str,
    command: list[str],
    env: dict[str, str],
    cwd: Path,
    timeout: float,
) -> str:
    payload = json.dumps(
        {
            "name": name,
            "command": command,
            "env": env,
            "cwd": str(cwd.resolve()),
            "timeout": timeout,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{name}@sha256:{hashlib.sha256(payload).hexdigest()}"


def _validate_mcp_name(name: str, kind: str) -> None:
    if not MCP_NAME_RE.fullmatch(name):
        raise MCPConfigError(
            f"invalid MCP {kind} name '{name}'; use letters, digits, underscores, or hyphens"
        )
