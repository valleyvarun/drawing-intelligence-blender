"""Bridge requests run on Blender's main thread: tool listings and tool calls as MCP results."""

from __future__ import annotations

import base64
from pathlib import Path

from .. import api
from ..api.common import viewport
from ..api.registry import Image, Tool, ToolError, get, to_json
from .transcript import JsonObject, mapping, text


def _order(entry: Tool) -> tuple[bool, bool]:
    """Inspection tools first, then edits; tools that need approval last."""
    return entry.approval, not entry.read_only


def tool_definitions() -> list[JsonObject]:
    definitions: list[JsonObject] = []
    entry: Tool
    for entry in sorted(api.tools(), key=_order):
        definitions.append(entry.definition())
    return definitions


def content(result: object) -> list[JsonObject]:
    """MCP content blocks for a tool's return value; images are sent as pictures."""
    if isinstance(result, Image):
        path: Path = Path(result.path)
        data: str = base64.b64encode(path.read_bytes()).decode("ascii")
        try:
            path.unlink()
        except OSError:
            pass
        return [{"type": "image", "data": data, "mimeType": result.mime_type}]
    if isinstance(result, str):
        return [{"type": "text", "text": result}]
    return [{"type": "text", "text": to_json(result)}]


def run_tool(name: str, arguments: JsonObject) -> JsonObject:
    """One agent tool call: run it in the viewport context and make edits a single undo step."""
    from ..core import controller
    from ..core.state import get_state

    entry: Tool
    try:
        entry = get(name)
    except ToolError as error:
        return {"content": [{"type": "text", "text": str(error)}], "isError": True}
    try:
        with viewport():
            result: object = api.call(name, arguments)
            if entry.undo:
                controller.record_undo("Agent: " + entry.undo)
    except ToolError as error:
        return {"content": [{"type": "text", "text": str(error)}], "isError": True}
    # A failing tool must not stop the app; the agent sees the error and can adjust.
    except Exception as error:
        return {"content": [{"type": "text", "text": f"{type(error).__name__}: {error}"}], "isError": True}
    finally:
        controller.redraw()
    if not entry.read_only:
        get_state().status = "Agent: " + name.replace("_", " ")
    return {"content": content(result), "isError": False}


def handle(method: str, params: JsonObject) -> JsonObject:
    """Bridge handler: requests from MCP server processes."""
    if method == "list_tools":
        return {"tools": tool_definitions()}
    if method == "call_tool":
        return run_tool(text(params.get("name")), mapping(params.get("arguments")))
    if method == "ping":
        return {}
    raise ValueError(f"Unknown bridge method: {method}")
