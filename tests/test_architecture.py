"""Guards for the modular structure: Streamlit stays in `ui`, layers only depend downwards, app.py stays thin."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "market_intelligence"

# Layer order, lowest first. A module may import its own layer or a lower one, never a higher one.
LAYERS = [
    ("config", "util"),
    ("infra", "models", "provenance"),
    ("data", "history", "replenishment", "scenarios"),
    ("collectors", "persistence", "signals", "recalls", "weather", "demand"),
    ("incidents", "llm"),
    ("application",),
    ("ui",),
]
LAYER_OF = {name: index for index, group in enumerate(LAYERS) for name in group}

# Known, deliberate upward imports (pure view-model helpers living under ui/ that do not import Streamlit).
ALLOWED_UPWARD = {
    ("weather/inbox_helpers.py", "market_intelligence.ui.operational_impact"),
}


def _modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _imports(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_streamlit_is_only_imported_by_the_ui_package() -> None:
    offenders = []
    for path, tree in _modules():
        if path.relative_to(PACKAGE).parts[0] == "ui":
            continue
        if any(name == "streamlit" or name.startswith("streamlit.") for name in _imports(tree)):
            offenders.append(str(path.relative_to(PACKAGE)))
    assert not offenders, f"Streamlit imported outside ui/: {offenders}"


def test_layers_only_depend_downwards() -> None:
    violations = []
    for path, tree in _modules():
        rel = path.relative_to(PACKAGE)
        own = LAYER_OF.get(rel.parts[0])
        if own is None:
            continue
        for name in _imports(tree):
            parts = name.split(".")
            if parts[0] != "market_intelligence" or len(parts) < 2:
                continue
            target = LAYER_OF.get(parts[1])
            if target is None or target <= own:
                continue
            if (rel.as_posix(), ".".join(parts[:3])) in ALLOWED_UPWARD:
                continue
            violations.append(f"{rel.as_posix()} imports {name}")
    assert not violations, "Upward imports:\n" + "\n".join(violations)


def test_ui_helpers_imported_from_lower_layers_do_not_use_streamlit() -> None:
    for relative in ("ui/operational_impact.py",):
        tree = ast.parse((PACKAGE / relative).read_text(encoding="utf-8"))
        assert not any(n == "streamlit" or n.startswith("streamlit.") for n in _imports(tree)), relative


def test_app_py_is_only_wiring() -> None:
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    assert not [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    assert len((ROOT / "app.py").read_text(encoding="utf-8").splitlines()) < 100
