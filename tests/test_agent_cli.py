"""Finding Claude Code: PATH, standalone installs, npm shims, the Claude desktop app, and editor extensions."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT: Path = Path(__file__).resolve().parents[1]

NPM_SHIM: str = """@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\\node.exe" (
  SET "_prog=%dp0%\\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\{target}" %*
"""


def load_cli() -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location("_cli", ROOT / "app/drawing_app/agent/cli.py")
    assert spec is not None and spec.loader is not None
    result: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class ClaudeCodeDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cli: ModuleType = load_cli()
        self.folder: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        base: Path = Path(self.folder.name)
        self.home: Path = base / "home"
        self.local: Path = base / "local"
        self.roaming: Path = base / "roaming"
        self.saved: str = ""
        folder: Path
        for folder in (self.home, self.local, self.roaming):
            folder.mkdir()
        self.addCleanup(self.folder.cleanup)
        # Only these folders exist for the search: no real PATH, profile, or saved Windows PATH.
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "PATH": "",
                    "HOME": str(self.home),
                    "USERPROFILE": str(self.home),
                    "LOCALAPPDATA": str(self.local),
                    "APPDATA": str(self.roaming),
                    "DRAWING_CLAUDE_PATH": "",
                },
            )
        )
        self.enterContext(patch.object(self.cli, "_saved_path", self.saved_windows_path))

    def saved_windows_path(self) -> str:
        return self.saved

    @staticmethod
    def touch(path: Path, text: str = "") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def desktop(self, version: str, build: str) -> Path:
        package: Path = self.local / "Claude-Data" / "claude_pzs8sxrjxfjjc" / "claude-code"
        return self.touch(package / version / build / "claude.exe")

    def editor(self, version: str) -> Path:
        folder: Path = self.home / ".vscode" / "extensions" / f"anthropic.claude-code-{version}-win32-x64"
        return self.touch(folder / "resources" / "native-binary" / "claude.exe")

    def test_nothing_installed_is_not_found(self) -> None:
        self.assertIsNone(self.cli.find())

    def test_desktop_app_copy_is_found_newest_first(self) -> None:
        self.desktop("2.1.317", "a33aa5518497")
        newest: Path = self.desktop("2.1.321", "d020ce1b5d30")
        self.assertEqual(self.cli.find(), [str(newest)])

    def test_newest_bundled_copy_wins_between_desktop_app_and_editor(self) -> None:
        desktop: Path = self.desktop("2.1.321", "d020ce1b5d30")
        self.editor("2.1.319")
        self.assertEqual(self.cli.find(), [str(desktop)])
        editor: Path = self.editor("2.10.0")
        self.assertEqual(self.cli.find(), [str(editor)])

    def test_standalone_install_comes_before_bundled_copies(self) -> None:
        self.desktop("2.1.321", "d020ce1b5d30")
        native: Path = self.touch(self.home / ".local" / "bin" / "claude.exe")
        self.assertEqual(self.cli.find(), [str(native)])

    def test_saved_windows_path_finds_an_install_made_after_the_app_started(self) -> None:
        folder: Path = self.home / "tools"
        installed: Path = self.touch(folder / "claude.exe")
        self.saved = str(folder)
        self.assertEqual(self.cli.find(), [str(installed)])

    def test_npm_shim_runs_its_javascript_target_with_its_node(self) -> None:
        npm: Path = self.roaming / "npm"
        target: Path = self.touch(npm / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js")
        node: Path = self.touch(npm / "node.exe")
        self.touch(npm / "claude.cmd", NPM_SHIM.replace("{target}", "node_modules\\@anthropic-ai\\claude-code\\cli.js"))
        self.assertEqual(self.cli.find(), [str(node), str(target)])

    def test_npm_shim_with_a_native_target_runs_it_directly(self) -> None:
        npm: Path = self.roaming / "npm"
        target: Path = self.touch(npm / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe")
        self.touch(
            npm / "claude.cmd",
            NPM_SHIM.replace("{target}", "node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"),
        )
        self.assertEqual(self.cli.find(), [str(target)])

    def test_missing_chosen_copy_falls_back_to_the_search(self) -> None:
        desktop: Path = self.desktop("2.1.321", "d020ce1b5d30")
        chosen: Path = self.touch(self.home / "chosen" / "claude.exe")
        self.assertEqual(self.cli.find(str(chosen)), [str(chosen)])
        chosen.unlink()
        self.assertEqual(self.cli.find(str(chosen)), [str(desktop)])

    @unittest.skipUnless(sys.platform == "win32", "Windows cannot start extensionless scripts")
    def test_extensionless_npm_script_cannot_be_chosen_on_windows(self) -> None:
        script: Path = self.touch(self.roaming / "npm" / "claude", "#!/bin/sh\n")
        self.assertIsNone(self.cli.command(str(script)))
        self.assertIsNone(self.cli.command(str(self.home / "missing.exe")))
        program: Path = self.editor("2.1.319")
        self.assertEqual(self.cli.command(str(program)), [str(program)])


if __name__ == "__main__":
    unittest.main()
