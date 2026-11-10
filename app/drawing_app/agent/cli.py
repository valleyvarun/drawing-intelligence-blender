"""Locating the Claude Code CLI and the Python interpreter that runs the MCP server."""

from __future__ import annotations

import os
import re
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import TypeAlias

# A Claude Code binary and its version, from the folder that holds it.
Bundled: TypeAlias = tuple[tuple[int, ...], Path]

INSTALL_HINT: str = (
    "Claude Code was not found. Install it from https://claude.com/claude-code (the Claude desktop app and the "
    "Claude Code extension for VS Code also include it), then try again, or choose claude.exe with Locate Claude Code."
)
# npm and pnpm .cmd shims name their target relative to the shim's folder.
_SHIM_TARGET: re.Pattern[str] = re.compile(r'"%~?dp0%?\\([^"]+)"')
_SCRIPTS: tuple[str, ...] = (".js", ".mjs", ".cjs")


def _version(name: str) -> tuple[int, ...]:
    """Version numbers in a folder name: anthropic.claude-code-2.1.319-win32-x64, or 2.1.321 alone."""
    match: re.Match[str] | None = re.search(r"(?:^|claude-code-)(\d+(?:\.\d+)*)", name)
    if match is None:
        return ()
    numbers: list[int] = []
    part: str
    for part in match.group(1).split("."):
        numbers.append(int(part))
    return tuple(numbers)


def _from_npm_shim(shim: Path) -> list[str] | None:
    """npm installs a .cmd shim; run its target directly, since cmd.exe would re-parse the arguments."""
    package: Path = shim.parent / "node_modules" / "@anthropic-ai" / "claude-code"
    targets: list[Path] = []
    try:
        script: str = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        script = ""
    named: list[str] = []
    found: str
    for found in _SHIM_TARGET.findall(script):
        if Path(found).name.lower() != "node.exe":
            named.append(found)
    # The last path the shim names is what it runs, such as node_modules\@anthropic-ai\claude-code\cli.js.
    if named:
        targets.append(shim.parent / named[-1])
    targets += [package / "bin" / "claude.exe", package / "claude.exe", package / "cli.js"]
    local_node: Path = shim.parent / "node.exe"
    node: str | None = str(local_node) if local_node.is_file() else shutil.which("node")
    target: Path
    for target in targets:
        if not target.is_file():
            continue
        suffix: str = target.suffix.lower()
        if suffix == ".exe":
            return [str(target)]
        if suffix in _SCRIPTS and node:
            return [node, str(target)]
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
    if sys.platform == "win32" and suffix != ".exe":
        # npm's extensionless shell script is for Git Bash; Windows cannot start it.
        return None
    return [str(path)]


def command(path: str) -> list[str] | None:
    """How to start Claude Code from a chosen file; None when that file cannot start it."""
    return _command(Path(path).expanduser())


def _saved_path() -> str:
    """PATH as Windows saved it, so Claude Code installed after this app started is still found."""
    if sys.platform != "win32":
        return ""
    import winreg

    # The user's PATH, then the machine's.
    keys: tuple[tuple[int, str], ...] = (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    )
    entries: list[str] = []
    root: int
    key: str
    for root, key in keys:
        try:
            with winreg.OpenKey(root, key) as handle:
                value: object = winreg.QueryValueEx(handle, "Path")[0]
        except OSError:
            continue
        if isinstance(value, str) and value:
            entries.append(os.path.expandvars(value))
    return os.pathsep.join(entries)


def _desktop_binaries() -> list[Bundled]:
    """Copies the Claude desktop app keeps, such as Claude-Data\\claude_…\\claude-code\\2.1.321\\<id>\\claude.exe."""
    roots: list[Path] = []
    local: str = os.environ.get("LOCALAPPDATA", "")
    roaming: str = os.environ.get("APPDATA", "")
    if local:
        # The Microsoft Store app keeps its data under its package name.
        roots.extend(Path(local, "Claude-Data").glob("*/claude-code"))
    if roaming:
        roots.append(Path(roaming, "Claude", "claude-code"))
    found: list[Bundled] = []
    root: Path
    for root in roots:
        if not root.is_dir():
            continue
        pattern: str
        for pattern in ("*/claude.exe", "*/*/claude.exe"):
            candidate: Path
            for candidate in root.glob(pattern):
                found.append((_version(candidate.relative_to(root).parts[0]), candidate))
    return found


def _editor_binaries(home: Path) -> list[Bundled]:
    """Claude Code binaries bundled with VS Code-family editor extensions."""
    found: list[Bundled] = []
    editor: str
    for editor in (".vscode", ".vscode-insiders", ".cursor", ".windsurf"):
        root: Path = home / editor / "extensions"
        if not root.is_dir():
            continue
        folder: Path
        for folder in root.glob("anthropic.claude-code-*"):
            name: str
            for name in ("claude.exe", "claude"):
                candidate: Path = folder / "resources" / "native-binary" / name
                if candidate.is_file():
                    found.append((_version(folder.name), candidate))
    return found


def _newest(item: Bundled) -> tuple[int, ...]:
    return item[0]


def _candidates(home: Path) -> Iterator[Path]:
    """Where Claude Code may be, in order: PATH, standalone installs, then copies bundled with other apps."""
    search: str
    for search in (os.environ.get("PATH", ""), _saved_path()):
        if not search:
            continue
        name: str
        for name in ("claude.exe", "claude"):
            found: str | None = shutil.which(name, path=search)
            if found:
                yield Path(found)
    # The native installer, npm's default global folder, and WinGet's links.
    yield home / ".local" / "bin" / "claude.exe"
    yield home / ".local" / "bin" / "claude"
    roaming: str = os.environ.get("APPDATA", "")
    if roaming:
        yield Path(roaming, "npm", "claude.cmd")
    local: str = os.environ.get("LOCALAPPDATA", "")
    if local:
        yield Path(local, "Microsoft", "WinGet", "Links", "claude.exe")
    bundled: list[Bundled] = [*_desktop_binaries(), *_editor_binaries(home)]
    bundled.sort(key=_newest, reverse=True)
    item: Bundled
    for item in bundled:
        yield item[1]


def find(configured: str = "") -> list[str] | None:
    """Command that starts Claude Code: a chosen path, PATH, a standalone install, the desktop app, or an editor."""
    explicit: str = configured or os.environ.get("DRAWING_CLAUDE_PATH", "")
    chosen: list[str] | None = command(explicit) if explicit else None
    if chosen is not None:
        return chosen
    # A chosen copy can disappear, as when the desktop app updates; the usual places are searched instead.
    candidate: Path
    for candidate in _candidates(Path.home()):
        found: list[str] | None = _command(candidate)
        if found is not None:
            return found
    return None


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
