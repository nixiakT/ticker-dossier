"""Read-only defaults shipped with every TickerDossier installation."""
from __future__ import annotations

from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path


def bundled_skills_root() -> Traversable:
    """Return the package-backed directory containing built-in Skills."""
    return files(__package__).joinpath("skills")


def bundled_mcp_config() -> Traversable:
    """Return the package-backed MCP template used outside a checkout."""
    return files(__package__).joinpath("default.mcp.json")


def materialize_project_defaults(
    destination: str | Path = ".",
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Copy editable Skill defaults and ``.mcp.json`` into a project.

    Existing files are preserved unless ``overwrite`` is explicitly enabled.
    The returned paths are the files written during this call.
    """
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    _copy_resource_tree(
        bundled_skills_root(),
        root / "skills",
        overwrite=overwrite,
        written=written,
    )
    _copy_resource_file(
        bundled_mcp_config(),
        root / ".mcp.json",
        overwrite=overwrite,
        written=written,
    )
    return written


def _copy_resource_tree(
    source: Traversable,
    destination: Path,
    *,
    overwrite: bool,
    written: list[Path],
) -> None:
    for child in sorted(source.iterdir(), key=lambda item: item.name):
        target = destination / child.name
        if child.is_dir():
            _copy_resource_tree(child, target, overwrite=overwrite, written=written)
        elif child.is_file():
            _copy_resource_file(child, target, overwrite=overwrite, written=written)


def _copy_resource_file(
    source: Traversable,
    destination: Path,
    *,
    overwrite: bool,
    written: list[Path],
) -> None:
    if destination.exists() and not overwrite:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    written.append(destination)


__all__ = [
    "bundled_mcp_config",
    "bundled_skills_root",
    "materialize_project_defaults",
]
