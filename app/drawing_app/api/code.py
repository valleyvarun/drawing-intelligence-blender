"""Running Python inside the app: anything the other tools do not cover."""

from __future__ import annotations

import ast
import contextlib
import io
import traceback
from typing import Any

from ..core.blender import bpy
from .common import viewport
from .registry import JsonObject, ToolError, to_json, tool

# Keep replies readable for agents; scripts can print less or write files for more.
OUTPUT_LIMIT: int = 20000


def _clip(text: str) -> str:
    return (
        text if len(text) <= OUTPUT_LIMIT else text[:OUTPUT_LIMIT] + f"\n… ({len(text) - OUTPUT_LIMIT} more characters)"
    )


def _value(value: object) -> object:
    """A JSON-friendly form of a result, or its repr."""
    try:
        to_json(value)
    except (TypeError, ValueError):
        return repr(value)
    return value if isinstance(value, (dict, list, str, int, float, bool)) or value is None else repr(value)


@tool(undo="Run Python", approval=True)
def execute_python(code: str) -> JsonObject:
    """Run Python inside the app with full access to Blender (bpy) and the drawing API.

    The value of a final expression is returned with anything printed. Prefer the other tools when
    they fit; use this for anything else, such as modifiers, materials, or bulk edits.

    Args:
        code: Python source. `bpy`, `api` (drawing_app.api), and `context` are already defined.
    """
    from .. import api

    try:
        tree: ast.Module = ast.parse(code, "<agent>", "exec")
    except SyntaxError as error:
        raise ToolError(f"Syntax error on line {error.lineno}: {error.msg}") from error
    # A final expression is evaluated separately, so its value can be returned.
    final: ast.expr | None = None
    last: ast.stmt | None = tree.body[-1] if tree.body else None
    if isinstance(last, ast.Expr):
        tree.body.pop()
        final = last.value
    output: io.StringIO = io.StringIO()
    result: object = None
    context: Any
    with viewport() as context:
        namespace: dict[str, object] = {"__name__": "__agent__", "bpy": bpy, "api": api, "context": context}
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                exec(compile(tree, "<agent>", "exec"), namespace)
                if final is not None:
                    result = eval(compile(ast.Expression(final), "<agent>", "eval"), namespace)
        # Any error in the script is reported back, starting at the script's own frames.
        except Exception as error:
            frames: Any = error.__traceback__.tb_next if error.__traceback__ is not None else None
            details: str = "".join(traceback.format_exception(type(error), error, frames))
            raise ToolError(_clip(output.getvalue() + details)) from error
    return {"result": _value(result), "output": _clip(output.getvalue())}
