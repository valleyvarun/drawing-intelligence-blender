"""Compile app sources and reproduce the host editor's type-checking context."""

from __future__ import annotations

import json
import py_compile
import re
from pathlib import Path
from typing import Any

APP: Path = Path(__file__).resolve().parents[1]
OUTPUT: Path = APP / ".checks"


def editor_repository() -> Path:
    """Find editor settings in either the standalone or enclosing checkout."""
    candidate: Path
    for candidate in (APP, *APP.parents):
        if (candidate / ".vscode/settings.json").is_file():
            return candidate
    return APP


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    source_files: list[Path] = []
    directory: Path
    path: Path
    for directory in (APP / "app", APP / "tests", APP / "scripts"):
        for path in directory.rglob("*.py"):
            if ".checks" not in path.parts:
                source_files.append(path)
    for path in source_files:
        relative: Path = path.relative_to(APP)
        target: Path = OUTPUT / "syntax" / relative.with_suffix(".pyc")
        target.parent.mkdir(parents=True, exist_ok=True)
        py_compile.compile(str(path), cfile=str(target), doraise=True)
    repository: Path = editor_repository()
    settings_path: Path = repository / ".vscode/settings.json"
    if not settings_path.is_file():
        print(json.dumps({"syntax_files": len(source_files), "editor_context": "not present"}))
        return
    raw: str = settings_path.read_text(encoding="utf-8-sig")
    raw = re.sub(r"^\s*//.*$", "", raw, flags=re.MULTILINE)
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    settings: dict[str, Any] = json.loads(raw)
    paths: list[str] = []
    entry: str
    for entry in settings.get("python.analysis.extraPaths", []):
        path = Path(entry.replace("${workspaceFolder}", str(repository)))
        paths.append(str(path if path.is_absolute() else repository / path))
    config: dict[str, object] = {
        "include": ["../app", "../tests", "../scripts"],
        "exclude": ["../app/**/.checks"],
        "pythonVersion": "3.12",
        "typeCheckingMode": settings.get("python.analysis.typeCheckingMode", "basic"),
        "extraPaths": paths,
        **settings.get("python.analysis.diagnosticSeverityOverrides", {}),
    }
    (OUTPUT / "editor-pyrightconfig.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(json.dumps({"syntax_files": len(source_files), "exact_editor_extra_paths": len(paths)}))


main()
