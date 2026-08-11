"""Backward-compatible facade for the split MCP implementation.

New code should import transport, configuration, or runtime concerns from the
focused sibling modules.  This facade deliberately preserves the original
public and test-facing private names while downstream callers migrate.
"""
from __future__ import annotations

from ticker_dossier.integrations.mcp.config import (
    BUILTIN_PROJECT_MCP_MODULES,
    DEFAULT_TIMEOUT,
    LEGACY_TRUSTED_PROJECT_MCP_ENV,
    MCP_ARGUMENT_NAME_RE,
    MCP_INHERITED_ENV,
    MCP_NAME_RE,
    TRUSTED_PROJECT_MCP_ENV,
    MCPConfigError,
    _default_mcp_config_path,
    _is_builtin_project_server,
    _mcp_process_env,
    _mcp_trust_token,
    _package_import_root,
    _validate_mcp_name,
)
from ticker_dossier.integrations.mcp.runtime import (
    MCPInspection,
    MCPRuntime,
    _client_from_spec,
    _discover_prompts,
    _load_project_clients,
    _mcp_segment,
    _sanitize_mcp_schema,
    _tool_runner,
    connect_project_mcp,
    default_echo_client,
    inspect_project_mcp,
    register_mcp_tools,
)
from ticker_dossier.integrations.mcp.transport import _EOF, MCPClient, MCPRPCError


__all__ = [
    "BUILTIN_PROJECT_MCP_MODULES",
    "DEFAULT_TIMEOUT",
    "LEGACY_TRUSTED_PROJECT_MCP_ENV",
    "MCP_ARGUMENT_NAME_RE",
    "MCP_INHERITED_ENV",
    "MCP_NAME_RE",
    "TRUSTED_PROJECT_MCP_ENV",
    "MCPClient",
    "MCPConfigError",
    "MCPInspection",
    "MCPRPCError",
    "MCPRuntime",
    "_EOF",
    "_client_from_spec",
    "_default_mcp_config_path",
    "_discover_prompts",
    "_is_builtin_project_server",
    "_load_project_clients",
    "_mcp_process_env",
    "_mcp_segment",
    "_mcp_trust_token",
    "_package_import_root",
    "_sanitize_mcp_schema",
    "_tool_runner",
    "_validate_mcp_name",
    "connect_project_mcp",
    "default_echo_client",
    "inspect_project_mcp",
    "register_mcp_tools",
]
