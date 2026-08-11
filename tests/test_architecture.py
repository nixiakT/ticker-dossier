from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "ticker_dossier"


def _project_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    relative = path.relative_to(PACKAGE_ROOT)
    package_parts = ("ticker_dossier", *relative.parent.parts)
    package = ".".join(package_parts)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                imported = resolve_name("." * node.level + (node.module or ""), package)
                imports.add(imported)
                if node.module is None:
                    imports.update(f"{imported}.{alias.name}" for alias in node.names)
            elif node.module:
                imports.add(node.module)
    return {name for name in imports if name == "ticker_dossier" or name.startswith("ticker_dossier.")}


def _assert_layer_avoids(layer: str, forbidden: tuple[str, ...]) -> None:
    violations: list[str] = []
    for path in sorted((PACKAGE_ROOT / layer).rglob("*.py")):
        for imported in sorted(_project_imports(path)):
            if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden):
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {imported}")
    assert not violations, "forbidden dependency direction:\n" + "\n".join(violations)


def test_architecture_scanner_resolves_relative_imports() -> None:
    imports = _project_imports(PACKAGE_ROOT / "research" / "debate" / "orchestrator.py")

    assert "ticker_dossier.research.protocols" in imports


def test_runtime_does_not_import_concrete_capabilities() -> None:
    _assert_layer_avoids(
        "runtime",
        (
            "ticker_dossier.cli",
            "ticker_dossier.integrations",
            "ticker_dossier.integrations.llm",
            "ticker_dossier.research",
            "ticker_dossier.skills",
            "ticker_dossier.tools",
        ),
    )


def test_research_does_not_import_outer_adapters_or_concrete_models() -> None:
    _assert_layer_avoids(
        "research",
        ("ticker_dossier.cli", "ticker_dossier.integrations.llm", "ticker_dossier.tools"),
    )


def test_market_data_providers_do_not_import_product_workflows() -> None:
    forbidden = (
        "ticker_dossier.cli",
        "ticker_dossier.portfolio",
        "ticker_dossier.research",
        "ticker_dossier.runtime",
        "ticker_dossier.tools",
    )
    violations: list[str] = []
    adapter_root = PACKAGE_ROOT / "market_data" / "providers"
    for path in sorted(adapter_root.rglob("*.py")):
        for imported in sorted(_project_imports(path)):
            if any(imported == prefix or imported.startswith(prefix + ".") for prefix in forbidden):
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {imported}")

    assert not violations, "market-data provider depends on a product workflow:\n" + "\n".join(violations)


def test_feature_packages_have_one_canonical_home() -> None:
    removed_paths = (
        PACKAGE_ROOT / "integrations" / "market_data",
        PACKAGE_ROOT / "research" / "data.py",
        PACKAGE_ROOT / "research" / "market_data",
        PACKAGE_ROOT / "research" / "paper_portfolio.py",
        PACKAGE_ROOT / "research" / "portfolio",
    )

    lingering_sources = [
        path
        for path in removed_paths
        if path.is_file() or (path.is_dir() and any(path.rglob("*.py")))
    ]

    assert not lingering_sources
