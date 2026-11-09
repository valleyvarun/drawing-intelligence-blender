"""The shortcut table in app/drawing_app/core/shortcuts.py: matching, conflicts, and known commands."""

from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType

ROOT: Path = Path(__file__).resolve().parents[1]
TOOLS: tuple[str, ...] = ("select", "move", "rotate", "scale", "paint", "pipe", "printer", "extruder", "grease")


def load() -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(
        "_shortcuts_test", ROOT / "app/drawing_app/core/shortcuts.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def dispatch_actions() -> set[str]:
    """Action names that controller.dispatch handles, read from its source."""
    tree: ast.Module = ast.parse((ROOT / "app/drawing_app/core/controller.py").read_text(encoding="utf-8"))
    found: set[str] = set()
    node: ast.AST
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "action":
            comparator: ast.expr
            for comparator in node.comparators:
                constant: ast.AST
                for constant in ast.walk(comparator):
                    if isinstance(constant, ast.Constant) and isinstance(constant.value, str):
                        found.add(constant.value)
    return found


class ShortcutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.shortcuts: ModuleType = load()

    def find(
        self, key: str, *, ctrl: bool = False, shift: bool = False, tool: str = "select", over_viewport: bool = True
    ) -> tuple[str, object]:
        found: object = self.shortcuts.shortcut_for(key, ctrl, shift, False, tool, over_viewport)
        self.assertIsNotNone(found, key)
        return getattr(found, "action"), getattr(found, "payload")

    def test_shift_a_opens_the_add_menu_anywhere(self) -> None:
        tool: str
        for tool in ("select", "paint", "grease"):
            self.assertEqual(self.find("A", shift=True, tool=tool), ("menu", "create"))
            self.assertEqual(self.find("A", shift=True, tool=tool, over_viewport=False), ("menu", "create"))
        # The row runs before the block on Blender's own Shift+A Add menu.
        self.assertTrue(self.shortcuts.is_blocked("A", False, True, False))

    def test_ctrl_shift_click_makes_a_surface_plane_only_in_the_viewport(self) -> None:
        tool: str
        for tool in ("select", "move", "paint", "pipe", "grease"):
            self.assertEqual(self.find("LEFTMOUSE", ctrl=True, shift=True, tool=tool), ("plane", "surface"))
        self.assertIsNone(self.shortcuts.shortcut_for("LEFTMOUSE", True, True, False, "select", False))
        # Plain and Shift clicks keep selecting and drawing.
        self.assertIsNone(self.shortcuts.shortcut_for("LEFTMOUSE", False, False, False, "select", True))
        self.assertIsNone(self.shortcuts.shortcut_for("LEFTMOUSE", False, True, False, "select", True))
        self.assertIsNone(self.shortcuts.shortcut_for("LEFTMOUSE", True, False, False, "select", True))

    def test_double_click_activates_a_plane_where_clicks_select(self) -> None:
        tool: str
        for tool in ("select", "move", "rotate", "scale"):
            found: object = self.shortcuts.shortcut_for("LEFTMOUSE", False, False, False, tool, True, "DOUBLE_CLICK")
            self.assertEqual((getattr(found, "action"), getattr(found, "payload")), ("plane", "clicked"))
        # Drawing tools draw on click; side panels rename on double-click.
        for tool in ("paint", "pipe", "grease"):
            self.assertIsNone(self.shortcuts.shortcut_for("LEFTMOUSE", False, False, False, tool, True, "DOUBLE_CLICK"))
        self.assertIsNone(
            self.shortcuts.shortcut_for("LEFTMOUSE", False, False, False, "select", False, "DOUBLE_CLICK")
        )
        # The first press of the double-click still selects.
        self.assertIsNone(self.shortcuts.shortcut_for("LEFTMOUSE", False, False, False, "select", True))
        # Keyboard rows answer presses, not a fast second tap.
        self.assertIsNone(self.shortcuts.shortcut_for("ONE", False, False, False, "select", True, "DOUBLE_CLICK"))

    def test_n_switches_navigation_once_per_press(self) -> None:
        self.assertEqual(self.find("N"), ("navigation", "next"))
        self.assertEqual(self.find("N", tool="grease", over_viewport=False), ("navigation", "next"))
        self.assertFalse(getattr(self.shortcuts.shortcut_for("N", False, False, False, "select", True), "repeat"))

    def test_modifiers_match_exactly(self) -> None:
        self.assertEqual(self.find("S", ctrl=True), ("file", "save"))
        self.assertEqual(self.find("S", ctrl=True, shift=True), ("file", "save_as"))
        self.assertEqual(self.find("Z", ctrl=True), ("edit", "undo"))
        self.assertEqual(self.find("Z", ctrl=True, shift=True), ("edit", "redo"))
        self.assertIsNone(self.shortcuts.shortcut_for("S", False, False, False, "select", True))
        self.assertIsNone(self.shortcuts.shortcut_for("ONE", True, False, False, "select", True))
        self.assertIsNone(self.shortcuts.shortcut_for("S", True, False, True, "select", True))

    def test_view_keys_are_on_the_number_row_and_numpad_without_repeat(self) -> None:
        key: str
        axis: str
        for key, axis in (("ONE", "X"), ("TWO", "Y"), ("THREE", "Z"), ("ZERO", "PERSPECTIVE")):
            self.assertEqual(self.find(key), ("view_axis", axis))
            pad: str = "NUMPAD_" + {"ONE": "1", "TWO": "2", "THREE": "3", "ZERO": "0"}[key]
            self.assertEqual(self.find(pad, tool="grease"), ("view_axis", axis))
            # Holding the key would flip the view side back and forth.
            self.assertFalse(getattr(self.shortcuts.shortcut_for(key, False, False, False, "select", True), "repeat"))

    def test_tool_limits(self) -> None:
        self.assertEqual(self.find("F"), ("frame", None))
        # F is Blender's brush radius in Grease Pencil Draw; numpad period still frames.
        self.assertIsNone(self.shortcuts.shortcut_for("F", False, False, False, "grease", True))
        self.assertEqual(self.find("NUMPAD_PERIOD", tool="grease"), ("frame", None))
        tool: str
        for tool in ("paint", "pipe", "grease"):
            self.assertIsNone(self.shortcuts.shortcut_for("X", False, False, False, tool, True))
            self.assertIsNone(self.shortcuts.shortcut_for("DEL", False, False, False, tool, True))
        self.assertEqual(self.find("DEL", tool="move"), ("edit", "delete"))

    def test_no_two_rows_claim_the_same_keys_for_the_same_tool(self) -> None:
        claimed: dict[tuple[str, bool, bool, bool, str, str], str] = {}
        row: object
        for row in self.shortcuts.SHORTCUTS:
            tool: str
            for tool in TOOLS:
                only: tuple[str, ...] = getattr(row, "only")
                if (only and tool not in only) or tool in getattr(row, "skip"):
                    continue
                combo: tuple[str, bool, bool, bool, str, str] = (
                    getattr(row, "key"),
                    getattr(row, "ctrl"),
                    getattr(row, "shift"),
                    getattr(row, "alt"),
                    getattr(row, "value"),
                    tool,
                )
                self.assertNotIn(combo, claimed, f"{combo} is already {claimed.get(combo)}")
                claimed[combo] = getattr(row, "description")

    def test_every_shortcut_runs_a_dispatch_command(self) -> None:
        known: set[str] = dispatch_actions()
        self.assertIn("view_axis", known)
        row: object
        for row in self.shortcuts.SHORTCUTS:
            self.assertIn(getattr(row, "action"), known, getattr(row, "description"))
            self.assertTrue(getattr(row, "description"), getattr(row, "key"))
        command: tuple[str, str, str]
        for command in self.shortcuts.COMMANDS:
            self.assertIn(command[0], known, command)

    def test_blocked_engine_keys(self) -> None:
        self.assertTrue(self.shortcuts.is_blocked("TAB", False, False, False))
        self.assertTrue(self.shortcuts.is_blocked("A", False, True, False))
        self.assertTrue(self.shortcuts.is_blocked("F3", False, False, False))
        self.assertTrue(self.shortcuts.is_blocked("LEFT_ARROW", True, False, False))
        # F2 renames in Clayers; ordinary letters pass to Blender's tools.
        self.assertFalse(self.shortcuts.is_blocked("F2", False, False, False))
        self.assertFalse(self.shortcuts.is_blocked("G", False, False, False))


if __name__ == "__main__":
    unittest.main()
