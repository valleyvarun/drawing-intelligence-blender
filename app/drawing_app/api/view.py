"""Viewport tools: screenshots, named views, and framing."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from ..core.blender import bpy
from .common import find_object, number, viewport
from .registry import Image, JsonObject, ToolError, tool

# View name -> axis the viewer looks back along, and its sign.
VIEWS: dict[str, tuple[int, int]] = {
    "front": (1, -1),
    "back": (1, 1),
    "right": (0, 1),
    "left": (0, -1),
    "top": (2, 1),
    "bottom": (2, -1),
}


def _view_area(context: Any) -> Any:
    area: Any = context.area
    if bpy.app.background or area is None or area.type != "VIEW_3D":
        raise ToolError("The viewport is not available (the app has no window)")
    return area


def _shrink(path: Path, size: int) -> None:
    """Scale a PNG down so its longest side is at most `size` pixels."""
    image: Any = bpy.data.images.load(str(path), check_existing=False)
    try:
        width: int = int(image.size[0])
        height: int = int(image.size[1])
        factor: float = size / max(width, height, 1)
        if factor < 1.0:
            image.scale(max(1, round(width * factor)), max(1, round(height * factor)))
            image.filepath_raw = str(path)
            image.file_format = "PNG"
            image.save()
    finally:
        bpy.data.images.remove(image)


@tool(read_only=True)
def viewport_screenshot(max_size: int = 800) -> Image:
    """Capture the 3D viewport as it looks now, to check visual results.

    Args:
        max_size: Longest side of the image in pixels (100 to 2000).
    """
    size: int = round(number(max_size, "max_size"))
    if not 100 <= size <= 2000:
        raise ToolError("max_size must be between 100 and 2000 pixels")
    folder: Path = Path(tempfile.gettempdir()) / "drawing-intelligence"
    folder.mkdir(parents=True, exist_ok=True)
    path: Path = folder / f"viewport-{time.time_ns()}.png"
    context: Any
    with viewport() as context:
        _view_area(context)
        bpy.ops.screen.screenshot_area(filepath=str(path))
    if not path.is_file():
        raise ToolError("The screenshot could not be saved")
    _shrink(path, size)
    return Image(str(path))


@tool()
def set_view(view: Literal["perspective", "front", "back", "right", "left", "top", "bottom"]) -> JsonObject:
    """Look at the drawing from a named direction, keeping the current view target.

    Args:
        view: "perspective", or an orthographic "front", "back", "right", "left", "top", or "bottom" view.
    """
    from ..viewport.navigation import align_axis
    from ..viewport.navigation import set_view as set_named_view

    context: Any
    with viewport() as context:
        region_3d: Any = _view_area(context).spaces.active.region_3d
        if view == "perspective":
            set_named_view(region_3d, "PERSPECTIVE")
        else:
            axis: int
            sign: int
            axis, sign = VIEWS[view]
            target: Any = region_3d.view_location.copy()
            align_axis(region_3d, axis, sign)
            region_3d.view_location = target
            region_3d.view_perspective = "ORTHO"
            region_3d.update()
    return {"view": view}


@tool()
def frame_objects(names: list[str] | None = None) -> JsonObject:
    """Zoom the viewport to show the named objects, or everything when no names are given.

    Args:
        names: Objects to frame; omit to frame the whole drawing.
    """
    context: Any
    with viewport() as context:
        _view_area(context)
        if not names:
            bpy.ops.view3d.view_all(center=False)
            return {"framed": "all"}
        targets: list[Any] = []
        name: str
        for name in names:
            targets.append(find_object(name))
        previous: list[Any] = list(context.selected_objects)
        active: Any = context.view_layer.objects.active
        obj: Any
        for obj in previous:
            obj.select_set(False)
        for obj in targets:
            obj.select_set(True)
        bpy.ops.view3d.view_selected(use_all_regions=False)
        for obj in targets:
            obj.select_set(False)
        for obj in previous:
            obj.select_set(True)
        context.view_layer.objects.active = active
    return {"framed": names}
