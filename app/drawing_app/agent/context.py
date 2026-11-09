"""Attachments made inside the app: uploaded files (images resized to fit Claude), and app context.

App context is what the user sees and has chosen: a screenshot of the viewport, the selected objects, or a
summary of the whole drawing, from the same tools Claude itself can call.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Literal, TypeAlias

from ..core.blender import bpy
from .attachments import IMAGE_LIMIT, IMAGE_SIDE, IMAGE_TYPES, Attachment, AttachmentError, from_file, image, text

ContextKind: TypeAlias = Literal["viewport", "selection", "scene"]
# Other image files Blender can open, sent as PNG or JPEG.
CONVERTIBLE: frozenset[str] = frozenset((".bmp", ".tif", ".tiff", ".tga", ".exr", ".hdr", ".dds", ".jp2", ".psd"))
# Longest side of a resized image: Claude scales larger images down to about this anyway.
LONG_SIDE: int = 1568
JPEG_QUALITY: int = 90


def _folder() -> Path:
    folder: Path = Path(tempfile.gettempdir()) / "drawing-intelligence"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _resized(path: Path) -> Attachment:
    """An image that is too large, too big a picture, or of another type, saved again as PNG or JPEG."""
    try:
        picture: Any = bpy.data.images.load(str(path), check_existing=False)
    except RuntimeError as error:
        raise AttachmentError(f"{path.name} could not be opened as an image") from error
    saved: list[Path] = []
    try:
        width: int = int(picture.size[0])
        height: int = int(picture.size[1])
        if width <= 0 or height <= 0:
            raise AttachmentError(f"{path.name} could not be opened as an image")
        factor: float = min(1.0, LONG_SIDE / max(width, height))
        if factor < 1.0:
            picture.scale(max(1, round(width * factor)), max(1, round(height * factor)))
        stamp: int = time.time_ns()
        target: Path = _folder() / f"attachment-{stamp}.png"
        picture.filepath_raw = str(target)
        picture.file_format = "PNG"
        picture.save()
        saved.append(target)
        data: bytes = target.read_bytes()
        media_type: str = "image/png"
        if len(data) > IMAGE_LIMIT:
            # Photographs compress far better as JPEG.
            target = _folder() / f"attachment-{stamp}.jpg"
            picture.filepath_raw = str(target)
            picture.file_format = "JPEG"
            picture.save(quality=JPEG_QUALITY)
            saved.append(target)
            data = target.read_bytes()
            media_type = "image/jpeg"
        return image(path.name, data, media_type, str(path))
    finally:
        bpy.data.images.remove(picture)
        item: Path
        for item in saved:
            item.unlink(missing_ok=True)


def _image_size(path: Path) -> tuple[int, int] | None:
    """The picture's size in pixels, or None when Blender cannot read it (as with GIF)."""
    try:
        picture: Any = bpy.data.images.load(str(path), check_existing=False)
    except RuntimeError:
        return None
    try:
        return int(picture.size[0]), int(picture.size[1])
    finally:
        bpy.data.images.remove(picture)


def attach_file(path: Path) -> Attachment:
    """A file from the computer; images are resized or converted when Claude could not take them as they are."""
    suffix: str = path.suffix.lower()
    if suffix in CONVERTIBLE:
        return _resized(path)
    if suffix in IMAGE_TYPES and path.is_file():
        size: tuple[int, int] | None = _image_size(path)
        too_wide: bool = size is not None and max(size) > IMAGE_SIDE
        if path.stat().st_size > IMAGE_LIMIT or too_wide:
            if size is None:
                raise AttachmentError(f"{path.name} is too large to send, and this app cannot resize it")
            return _resized(path)
    return from_file(path)


def _viewport() -> Attachment:
    from ..api.registry import Image, ToolError
    from ..api.view import viewport_screenshot

    try:
        shot: Image = viewport_screenshot(LONG_SIDE)
    except ToolError as error:
        raise AttachmentError(str(error)) from error
    path: Path = Path(shot.path)
    try:
        return image("Viewport screenshot", path.read_bytes(), shot.mime_type)
    finally:
        path.unlink(missing_ok=True)


def _selection() -> Attachment:
    from ..api.scene import object_info

    names: list[str] = []
    obj: Any
    for obj in bpy.context.scene.objects:
        if obj.select_get():
            names.append(str(obj.name))
    if not names:
        raise AttachmentError("Nothing is selected. Select objects first, then add them.")
    details: list[object] = []
    name: str
    for name in names[:50]:
        details.append(object_info(name))
    label: str = names[0] if len(names) == 1 else f"{len(names)} selected objects"
    return text(label, json.dumps({"selected_objects": details}, indent=1, ensure_ascii=False))


def _scene() -> Attachment:
    from ..api.scene import scene_info

    return text("Scene summary", json.dumps(scene_info(), indent=1, ensure_ascii=False))


def attach_context(kind: ContextKind) -> Attachment:
    """What the user sees or has chosen in the app, as an attachment."""
    if kind == "viewport":
        return _viewport()
    if kind == "selection":
        return _selection()
    return _scene()
