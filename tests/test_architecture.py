from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "ticker_dossier"


def _project_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return {name for name in imports if name == "ticker_dossier" or name.startswith("ticker_dossier.")}


def _assert_layer_avoids(layer: str, forbidden: tuple[str, ...]) -> None:
    violations: list[str] = []
    for path in sorted((PACKAGE_ROOT / layer).rglob("*.py")):
        for imported in sorted(_project_imports(path)):
            if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden):
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {imported}")
    assert not violations, "forbidden dependency direction:\n" + "\n".join(violations)


def test_runtime_does_not_import_concrete_capabilities() -> None:
    _assert_layer_avoids(
        "runtime",
        (
            "ticker_dossier.cli",
            "ticker_dossier.integrations",
            "ticker_dossier.llm",
            "ticker_dossier.research",
            "ticker_dossier.skills",
            "ticker_dossier.tools",
        ),
    )


def test_research_does_not_import_cli_or_tool_adapters() -> None:
    _assert_layer_avoids(
        "research",
        ("ticker_dossier.cli", "ticker_dossier.tools"),
    )
