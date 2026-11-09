"""Tool registry: schemas generated from signatures and docstrings, and validated calls."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Literal, cast
from unittest.mock import patch

ROOT: Path = Path(__file__).resolve().parents[1]
JsonObject = dict[str, object]


def module(name: str, path: str) -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    result: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: result}):
        spec.loader.exec_module(result)
    return result


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry: ModuleType = module("_registry", "app/drawing_app/api/registry.py")

    def test_schema_comes_from_signature_and_docstring(self) -> None:
        @self.registry.tool(undo="Draw sample")
        def sample(
            points: list[list[float]],
            kind: Literal["pipe", "paint"] = "pipe",
            radius: float | None = None,
            closed: bool = False,
            name: str = "Sample",
            count: int = 2,
            size: tuple[float, float, float] = (1.0, 1.0, 1.0),
        ) -> JsonObject:
            """Draw a sample stroke.

            It shows how docstrings
            become tool descriptions.

            Args:
                points: The path,
                    in metres.
                kind: Stroke kind.
                radius: Half the width.
            """
            return {"points": len(points), "kind": kind, "radius": radius, "closed": closed, "name": name}

        entry: object = self.registry.get("sample")
        definition: JsonObject = getattr(entry, "definition")()
        self.assertEqual(definition["name"], "sample")
        self.assertEqual(
            definition["description"], "Draw a sample stroke.\n\nIt shows how docstrings become tool descriptions."
        )
        self.assertEqual(definition["annotations"], {"readOnlyHint": False, "destructiveHint": False})
        schema: JsonObject = cast(JsonObject, definition["inputSchema"])
        self.assertEqual(schema["required"], ["points"])
        properties: dict[str, JsonObject] = cast(dict[str, JsonObject], schema["properties"])
        self.assertEqual(
            properties["points"],
            {
                "type": "array",
                "items": {"type": "array", "items": {"type": "number"}},
                "description": "The path, in metres.",
            },
        )
        self.assertEqual(
            properties["kind"],
            {"type": "string", "enum": ["pipe", "paint"], "description": "Stroke kind.", "default": "pipe"},
        )
        self.assertEqual(properties["radius"], {"type": "number", "description": "Half the width."})
        self.assertEqual(properties["closed"], {"type": "boolean", "default": False})
        self.assertEqual(properties["name"], {"type": "string", "default": "Sample"})
        self.assertEqual(properties["count"], {"type": "integer", "default": 2})
        self.assertEqual(
            properties["size"],
            {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "default": (1.0, 1.0, 1.0)},
        )
        self.assertEqual(getattr(entry, "undo"), "Draw sample")
        self.assertEqual(
            self.registry.call("sample", {"points": [[0, 0, 0]], "kind": "paint"}),
            {"points": 1, "kind": "paint", "radius": None, "closed": False, "name": "Sample"},
        )

    def test_calls_are_checked_and_read_only_tools_are_marked(self) -> None:
        @self.registry.tool(read_only=True)
        def look(name: str) -> str:
            """Describe one object.

            Args:
                name: Object name.
            """
            return "looked at " + name

        @self.registry.tool(undo="Run", approval=True)
        def run(code: str) -> str:
            """Run code."""
            return code

        self.assertEqual(self.registry.call("look", {"name": "Cube"}), "looked at Cube")
        self.assertEqual(getattr(self.registry.get("look"), "definition")()["annotations"]["readOnlyHint"], True)
        self.assertEqual(getattr(self.registry.get("run"), "definition")()["annotations"]["destructiveHint"], True)
        names: list[str] = []
        entry: object
        for entry in self.registry.tools():
            names.append(getattr(entry, "name"))
        self.assertEqual(names, ["look", "run"])
        error: type[Exception] = self.registry.ToolError
        with self.assertRaisesRegex(error, "needs: name"):
            self.registry.call("look", {})
        with self.assertRaisesRegex(error, "does not take: size"):
            self.registry.call("look", {"name": "Cube", "size": 2})
        with self.assertRaisesRegex(error, "Unknown tool 'missing'"):
            self.registry.call("missing", {})

    def test_unsupported_parameter_types_fail_at_registration(self) -> None:
        def odd(values: set[str]) -> None:
            """Uses a set."""

        with self.assertRaisesRegex(TypeError, "Unsupported tool parameter type"):
            self.registry.tool()(odd)


if __name__ == "__main__":
    unittest.main()
