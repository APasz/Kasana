"""Import-boundary contracts for independently runnable Kasana components."""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "kasana"


def test_kanvas_and_kestrel_only_use_katalog_public_surface() -> None:
    components = tuple(
        component
        for component in ("kanvas", "kestrel", "yukibot")
        if (_SOURCE_ROOT / component).is_dir()
    )
    for component in components:
        forbidden = [
            imported
            for imported in component_imports(component)
            if imported.startswith("kasana.katalog")
            and imported != "kasana.katalog.public"
            and not imported.startswith("kasana.katalog.public.")
        ]
        assert not forbidden, f"{component} imports Katalog internals: {forbidden}"


def test_kourier_only_uses_katalog_public_surface() -> None:
    forbidden = [
        imported
        for imported in component_imports("kourier")
        if imported.startswith("kasana.katalog")
        and imported != "kasana.katalog.public"
        and not imported.startswith("kasana.katalog.public.")
    ]
    assert not forbidden, f"Kourier imports Katalog internals: {forbidden}"


def test_shared_modules_do_not_depend_on_component_implementations() -> None:
    component_prefixes = (
        "kasana.kanvas",
        "kasana.kestrel",
        "kasana.katalog",
        "kasana.kourier",
    )
    forbidden = [
        imported
        for imported in component_imports("shared")
        if imported.startswith(component_prefixes)
    ]
    assert not forbidden, f"Shared modules import component implementations: {forbidden}"


def component_imports(component: str) -> Iterable[str]:
    for source_file in (_SOURCE_ROOT / component).rglob("*.py"):
        yield from module_imports(source_file)


def module_imports(source_file: Path) -> Iterable[str]:
    tree = ast.parse(source_file.read_text(), filename=str(source_file))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            for alias in node.names:
                if alias.name == "*":
                    yield node.module
                else:
                    yield f"{node.module}.{alias.name}"
