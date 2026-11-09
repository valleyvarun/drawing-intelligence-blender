"""Drawing Intelligence scripting API: the tools AI agents use through MCP, as Python functions.

Use it from Windows > Scripting (the text editor or the Python console)::

    from drawing_app import api

    print(api.scene_info())
    api.draw_stroke([(0, 0, 0), (1, 0, 0.5), (2, 1, 1)], kind="pipe", radius=0.03, color="#FF8000")

Lengths are metres and Z is up. `api.tools()` lists every tool, `help(api.draw_stroke)` explains one,
and `api.call("draw_stroke", {...})` runs one by name with JSON-style arguments, exactly as agents do.
"""

from __future__ import annotations

from .code import execute_python
from .drawing import (
    create_drawing_plane,
    create_group,
    draw_stroke,
    set_active_group,
    set_brush,
    set_color,
    set_tool,
)
from .registry import Image, Tool, ToolError, call, tools
from .scene import add_primitive, delete_objects, object_info, scene_info, select_objects, transform_object
from .view import frame_objects, set_view, viewport_screenshot

__all__: list[str] = [
    "Image",
    "Tool",
    "ToolError",
    "add_primitive",
    "call",
    "create_drawing_plane",
    "create_group",
    "delete_objects",
    "draw_stroke",
    "execute_python",
    "frame_objects",
    "object_info",
    "scene_info",
    "select_objects",
    "set_active_group",
    "set_brush",
    "set_color",
    "set_tool",
    "set_view",
    "tools",
    "transform_object",
    "viewport_screenshot",
]
