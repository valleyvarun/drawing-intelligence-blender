"""Locating the Claude Code CLI and the Python interpreter that runs the MCP server."""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

INSTALL_HINT: str = (
    "Claude Code was not found. Install it from https://claude.com/claude-code (or the Claude Code "
    "extension for VS Code), then try again, or choose claude.exe with Locate Claude Code."
)


def _version(folder: Path) -> tuple[int, ...]:
    """Version numbers in an editor extension folder name such as anthropic.claude-code-2.1.319-win32-x64."""
    match: re.Match[str] | None = re.search(r"claude-code-(\d+(?:\.\d+)*)", folder.name)
    if match is None:
        return ()
    numbers: list[int] = []
    part: str
    for part in match.group(1).split("."):
        numbers.append(int(part))
    return tuple(numbers)


def _from_npm_shim(shim: Path) -> list[str] | None:
    """npm installs a .cmd shim; run its package directly, since cmd.exe would re-parse the arguments."""
    package: Path = shim.parent / "node_modules" / "@anthropic-ai" / "claude-code"
    native: Path
    for native in (package / "bin" / "claude.exe", package / "claude.exe"):
        if native.is_file():
            return [str(native)]
    script: Path = package / "cli.js"
    node: str | None = shutil.which("node")
    if script.is_file() and node:
        return [node, str(script)]
    return None


def _command(path: Path) -> list[str] | None:
    if not path.is_file():
        return None
    suffix: str = path.suffix.lower()
    if suffix == ".py":
        # Development and tests: a stand-in CLI written in Python.
        return [sys.executable, str(path)]
    if suffix in (".cmd", ".bat"):
        return _from_npm_shim(path)
    return [str(path)]


def _editor_binaries(home: Path) -> list[Path]:
    """Claude Code binaries bundled with VS Code-family editor extensions, newest first."""
    folders: list[Path] = []
    editor: str
    for editor in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        root: Path = home / editor / "extensions"
        if root.is_dir():
            folders.extend(root.glob("anthropic.claude-code-*"))
    folders.sort(key=_version, reverse=True)
    binaries: list[Path] = []
    folder: Path
    for folder in folders:
        name: str
        for name in ("claude.exe", "claude"):
            candidate: Path = folder / "resources" / "native-binary" / name
            if candidate.is_file():
                binaries.append(candidate)
    return binaries


def find(configured: str = "") -> list[str] | None:
    """Command that starts Claude Code: a chosen path, PATH, the native installer, or an editor extension."""
    explicit: str = configured or os.environ.get("DRAWING_CLAUDE_PATH", "")
    if explicit:
        return _command(Path(explicit).expanduser())
    command: list[str] | None
    name: str
    for name in ("claude.exe", "claude"):
        found: str | None = shutil.which(name)
        if found:
            command = _command(Path(found))
            if command is not None:
                return command
    home: Path = Path.home()
    candidate: Path
    for candidate in (home / ".local" / "bin" / "claude.exe", home / ".local" / "bin" / "claude"):
        command = _command(candidate)
        if command is not None:
            return command
    bundled: list[Path] = _editor_binaries(home)
    return [str(bundled[0])] if bundled else None


def python_executable(engine_binary: str) -> str:
    """The engine's bundled Python, which runs the standard-library MCP server."""
    current: Path = Path(sys.executable)
    if current.name.lower().startswith("python") and current.is_file():
        return str(current)
    engine: Path = Path(engine_binary).resolve().parent
    candidate: Path
    for candidate in sorted(engine.glob("*/python/bin/python*.exe")) + sorted(engine.glob("*/python/bin/python3*")):
        if candidate.is_file():
            return str(candidate)
    return str(current)
