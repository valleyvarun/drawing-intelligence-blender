"""Erase and Trim for every kind of stroke, front-most first.

The Erase brushes and Trim act on Grease Pencil strokes and fills, 3D Paint, 3D Pipe, Pipe Fill, 3D Printer
prints, and 3D Extruder tubes, in every visible, unlocked drawing group. Under each dab of the eraser they first
find what is in front, as the view shows it: where one stroke covers another, only the covering one is erased,
and the one behind waits until nothing erasable covers it there. Other objects only hide what is behind them.

- Eraser Hard removes what lies under its circle: it cuts strokes, pipes, and tubes, clips 3D Paint, Pipe Fill,
  and Grease Pencil fills along the circle, and digs into prints.
- Eraser Soft fades Grease Pencil strokes and 3D Paint; it leaves 3D strokes and fills alone.
- Eraser Stroke deletes every stroke it touches.
- Trim removes the touched part of a stroke between the places where it meets other strokes in 3D, or the whole
  stroke when it meets none; fills and prints are removed whole.

Each drag is one undo step.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from ..core.blender import Vector, bpy, mathutils, view3d_utils
from ..core.state import Vec3, get_state
from ..viewport import fills
from . import extruder, extrusion, layers, pencil, pipe_edit, printer
from .drawing import store_samples
from .erase_geometry import (
    Layer,
    Polygon,
    Span,
    Vec2,
    circle,
    erase_spans,
    falloff,
    inside,
    mask_spans,
    np,
    outlines,
    resolve,
    span_points,
    subtract,
    trim_spans,
)
from .geometry import Sample
from .pipe_geometry import Network, build_network

Mode: TypeAlias = Literal["HARD", "SOFT", "STROKE", "TRIM"]

MESH_KINDS: tuple[str, ...] = ("paint", "pipe", "fill", "print", "extrusion")
# What each tool acts on: the soft eraser fades only flat strokes that have an opacity per point.
KINDS: dict[str, tuple[str, ...]] = {
    "HARD": ("grease", *MESH_KINDS),
    "STROKE": ("grease", *MESH_KINDS),
    "TRIM": ("grease", *MESH_KINDS),
    "SOFT": ("grease", "paint"),
}
# At equal depth, marks drawn on a surface are in front of fills, and fills in front of solid strokes.
RANKS: dict[str, int] = {"grease": 0, "paint": 0, "fill": 1, "pipe": 2, "print": 2, "extrusion": 2}
OTHER_RANK: int = 2
# Depths this many pixels apart count as level.
SLACK: float = 2.0
# Samples across the eraser's circle, at most, and its dabs along a drag, in radii.
GRID: int = 9
SPACING: float = 0.25
# Objects looked through under each sample.
HITS: int = 2
# The soft eraser takes this much opacity per dab at full strength: about half over one pass across a point.
FADE: float = 0.06
# A faded point or paint vertex is gone at this opacity.
FADE_LIMIT: float = 0.004
# Faces smaller than this share of the eraser's radius are erased whole rather than clipped.
SMALL_FACE: float = 0.5
# The Trim gesture is sampled this many pixels apart and touches strokes this close to it.
TRIM_STEP: float = 3.0
TRIM_REACH: float = 4.0
# A gesture ending this many pixels from its start is a lasso: Grease Pencil points inside it are touched too.
CLOSED: float = 36.0
# Grease Pencil's radius and opacity where a drawing has no such attribute.
DEFAULT_RADIUS: float = 0.01
# How each attribute type is read: property, values per element, and buffer type.
PROPERTIES: dict[str, tuple[str, int, Any]] = {
    "FLOAT": ("value", 1, np.float32),
    "INT": ("value", 1, np.int32),
    "INT8": ("value", 1, np.int32),
    "BOOLEAN": ("value", 1, np.bool_),
    "FLOAT_VECTOR": ("vector", 3, np.float32),
    "FLOAT_COLOR": ("color", 4, np.float32),
    "BYTE_COLOR": ("color", 4, np.float32),
    "FLOAT2": ("vector", 2, np.float32),
    "INT32_2D": ("value", 2, np.int32),
    "INT16_2D": ("value", 2, np.int32),
    "QUATERNION": ("value", 4, np.float32),
    "FLOAT4X4": ("value", 16, np.float32),
}
BLENDED: tuple[str, ...] = ("FLOAT", "FLOAT_VECTOR", "FLOAT_COLOR", "BYTE_COLOR", "FLOAT2", "QUATERNION", "FLOAT4X4")


class View:
    """The viewport's projection: world points to region pixels and view depth, and pixels to rays."""

    def __init__(self, region: Any, view: Any) -> None:
        self.region: Any = region
        self.view: Any = view
        self.width: float = float(region.width)
        self.height: float = float(region.height)
        self.matrix: Any = np.array(view.perspective_matrix, dtype=np.float64)
        self.camera: Any = np.array(view.view_matrix, dtype=np.float64)
        window: Any = np.array(view.window_matrix, dtype=np.float64)
        self.perspective: bool = bool(view.is_perspective)
        # World size of one pixel at depth 1 (perspective) or anywhere (orthographic).
        self.unit: float = 2.0 / max(abs(float(window[1, 1])) * self.height, 1e-12)

    def project(self, points: Any) -> tuple[Any, Any, Any]:
        """Pixels (N, 2), view depths (N,), and whether each point is in front of the camera."""
        world: Any = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        clip: Any = world @ self.matrix[:, :3].T + self.matrix[:, 3]
        w: Any = clip[:, 3]
        valid: Any = w > 1e-9
        safe: Any = np.where(valid, w, 1.0)
        pixels: Any = np.stack(
            ((clip[:, 0] / safe + 1.0) * 0.5 * self.width, (clip[:, 1] / safe + 1.0) * 0.5 * self.height), axis=1
        )
        return pixels, self.depth(world), valid

    def depth(self, points: Any) -> Any:
        world: Any = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        return -(world @ self.camera[2, :3] + self.camera[2, 3])

    def pixel(self, depth: float) -> float:
        """World size of one pixel at a view depth."""
        return self.unit * max(depth, 1e-9) if self.perspective else self.unit

    def slack(self, depth: float) -> float:
        return SLACK * self.pixel(depth) + 1e-7 * abs(depth)

    def ray(self, point: Vec2) -> tuple[Any, Any]:
        origin: Any = view3d_utils.region_2d_to_origin_3d(self.region, self.view, point)
        direction: Any = view3d_utils.region_2d_to_vector_3d(self.region, self.view, point).normalized()
        return origin, direction


def _read(attributes: Any, name: str, count: int, width: int, kind: Any, default: float) -> Any:
    """An attribute's values, or `default` everywhere when the drawing has none."""
    values: Any = np.full(count * width, default, dtype=kind)
    attribute: Any = attributes.get(name)
    if attribute is not None and len(attribute.data) == count and count:
        prop: str = PROPERTIES[str(attribute.data_type)][0]
        attribute.data.foreach_get(prop, values)
    return values.reshape(count, width) if width > 1 else values


def _matrix(value: Any) -> Any:
    return np.array(value, dtype=np.float64)


def _layer_matrix(obj: Any, layer: Any) -> Any:
    """A Grease Pencil layer's drawing space to world space, through its parent when it has one."""
    if layer.parent is not None:
        return _matrix(layer.parent.matrix_world @ layer.matrix_parent_inverse @ layer.matrix_local)
    return _matrix(obj.matrix_world @ layer.matrix_local)


def _transform(matrix: Any, points: Any) -> Any:
    return np.asarray(points, dtype=np.float64).reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]


def _scale(matrix: Any) -> float:
    return float(np.linalg.norm(matrix[:3, :3], axis=0).mean())


@dataclass
class Strokes:
    """One Grease Pencil drawing as the view sees it; rebuilt after each change."""

    obj: Any
    layer: Any
    index: int
    drawing: Any
    prefix: str
    group: str
    flat: bool
    editable: bool
    # Each stroke's original index in this drag: pieces cut from one stroke share it.
    origin: Any
    matrix: Any
    offsets: Any
    cyclic: Any
    line: Any
    fill: Any
    locked: Any
    local: Any
    world: Any
    radius: Any
    pixels: Any
    depth: Any
    reach: Any
    valid: Any
    bounds: Any

    def key(self, stroke: int) -> str:
        return f"{self.prefix}#{int(self.origin[stroke])}"

    def members(self, origin: int) -> list[int]:
        """The strokes cut from one original stroke."""
        return [int(index) for index in np.flatnonzero(self.origin == origin)]

    def points(self, stroke: int) -> tuple[int, int]:
        return int(self.offsets[stroke]), int(self.offsets[stroke + 1])


def _strokes(context: Any, view: View, obj: Any, index: int, layer: Any, drawing: Any, origin: Any = None) -> Strokes:
    count: int = len(drawing.strokes)
    offsets: Any = np.zeros(count + 1, dtype=np.int32)
    if count:
        drawing.curve_offsets.foreach_get("value", offsets)
    total: int = int(offsets[-1])
    attributes: Any = drawing.attributes
    local: Any = _read(attributes, "position", total, 3, np.float32, 0.0).astype(np.float64)
    radius: Any = _read(attributes, "radius", total, 1, np.float32, DEFAULT_RADIUS).astype(np.float64)
    cyclic: Any = _read(attributes, "cyclic", count, 1, np.bool_, 0.0).astype(bool)
    materials: Any = _read(attributes, "material_index", count, 1, np.int32, 0.0)
    matrix: Any = _layer_matrix(obj, layer)
    world: Any = _transform(matrix, local)
    radius = radius * _scale(matrix) + float(layer.radius_offset)
    pixels: Any
    depth: Any
    valid: Any
    pixels, depth, valid = view.project(world)
    size: Any = view.unit * np.maximum(depth, 1e-9) if view.perspective else view.unit
    reach: Any = np.maximum(radius / size, 1.0)
    line: Any = np.ones(count, dtype=bool)
    fill: Any = np.zeros(count, dtype=bool)
    locked: Any = np.zeros(count, dtype=bool)
    slots: Any = obj.material_slots
    stroke: int
    for stroke in range(count):
        slot: int = int(materials[stroke])
        material: Any = slots[slot].material if 0 <= slot < len(slots) else None
        style: Any = material.grease_pencil if material is not None else None
        if style is not None:
            line[stroke] = bool(style.is_stroke_visible) and not style.hide
            fill[stroke] = (
                bool(style.is_fill_visible) and not style.hide and int(offsets[stroke + 1] - offsets[stroke]) >= 3
            )
            locked[stroke] = bool(style.lock)
    bounds: Any = np.zeros((count, 4))
    if count and total:
        starts: Any = offsets[:-1]
        safe: Any = np.where(valid[:, None], pixels, np.nan)
        low: Any = safe - reach[:, None]
        high: Any = safe + reach[:, None]
        nonempty: Any = offsets[1:] > starts
        reduced: Any = np.minimum(starts, total - 1)
        bounds[:, 0] = np.fmin.reduceat(low[:, 0], reduced)
        bounds[:, 1] = np.fmin.reduceat(low[:, 1], reduced)
        bounds[:, 2] = np.fmax.reduceat(high[:, 0], reduced)
        bounds[:, 3] = np.fmax.reduceat(high[:, 1], reduced)
        bounds[~nonempty] = np.nan
    ordered: bool = str(obj.data.stroke_depth_order) == "2D"
    editable: bool = not layers.object_locked(context, obj) and not layer.lock
    return Strokes(
        obj,
        layer,
        index,
        drawing,
        f"gp:{obj.name}:{index}",
        obj.name,
        ordered,
        editable,
        np.arange(count) if origin is None or len(origin) != count else np.asarray(origin),
        matrix,
        offsets,
        cyclic,
        line,
        fill,
        locked,
        local,
        world,
        radius,
        pixels,
        depth,
        reach,
        valid,
        bounds,
    )


@dataclass(frozen=True)
class Piece:
    """A stroke of a rewritten drawing: point k lies t[k] of the way from old point a[k] to old point b[k].

    A fill's new outline gives its own `positions` (drawing space); its points take their other attributes from
    the old points a[k].
    """

    stroke: int
    a: Any
    b: Any
    t: Any
    opened: bool
    positions: Any = None


def _rewrite(strokes: Strokes, pieces: list[Piece]) -> Any:
    """Replace a drawing's strokes with pieces of them, carrying every point and stroke attribute along.

    Returns each new stroke's origin: the original stroke it was cut from.
    """
    drawing: Any = strokes.drawing
    saved: list[tuple[str, str, str, Any]] = []
    attribute: Any
    for attribute in drawing.attributes:
        kind: str = str(attribute.data_type)
        spec: tuple[str, int, Any] | None = PROPERTIES.get(kind)
        if spec is None or str(attribute.domain) not in ("POINT", "CURVE"):
            continue
        count: int = len(attribute.data)
        values: Any = np.zeros(count * spec[1], dtype=spec[2])
        if count:
            attribute.data.foreach_get(spec[0], values)
        saved.append((str(attribute.name), kind, str(attribute.domain), values.reshape(count, spec[1])))
    sizes: list[int] = [len(piece.a) for piece in pieces]
    drawing.remove_strokes()
    if not pieces:
        return np.zeros(0, dtype=np.int64)
    drawing.add_strokes(sizes)
    a: Any = np.concatenate([piece.a for piece in pieces])
    b: Any = np.concatenate([piece.b for piece in pieces])
    t: Any = np.concatenate([piece.t for piece in pieces])[:, None]
    sources: Any = np.array([piece.stroke for piece in pieces], dtype=np.int64)
    opened: Any = np.array([piece.opened for piece in pieces], dtype=bool)
    shaped: Any = np.concatenate([np.full(len(piece.a), piece.positions is not None) for piece in pieces])
    shapes: list[Any] = [piece.positions for piece in pieces if piece.positions is not None]
    name: str
    domain: str
    data: Any
    for name, kind, domain, data in saved:
        target: Any = drawing.attributes.get(name)
        if target is None:
            try:
                target = drawing.attributes.new(name, kind, domain)
            except (RuntimeError, TypeError, ValueError):
                continue
        spec = PROPERTIES[kind]
        result: Any
        if domain == "POINT":
            if kind in BLENDED:
                result = data[a] * (1.0 - t) + data[b] * t
            else:
                result = np.where(t < 0.5, data[a], data[b])
            if name == "position" and shapes:
                result[shaped] = np.concatenate(shapes)
        else:
            result = data[sources]
            if name == "cyclic":
                result = result & ~opened[:, None]
        target.data.foreach_set(spec[0], np.ascontiguousarray(result, dtype=spec[2]).ravel())
    return np.asarray(strokes.origin)[sources]


def _pieces(strokes: Strokes, plans: dict[int, list[Span]], shapes: dict[int, list[Any]] | None = None) -> list[Piece]:
    """Every stroke of a drawing, with planned strokes replaced by their kept spans and fills by their new outlines."""
    result: list[Piece] = []
    outlines_by_stroke: dict[int, list[Any]] = shapes or {}
    stroke: int
    for stroke in range(len(strokes.offsets) - 1):
        first: int
        last: int
        first, last = strokes.points(stroke)
        outline: list[Any] | None = outlines_by_stroke.get(stroke)
        if outline is not None:
            shape: Any
            for shape in outline:
                # Each new point takes its other attributes from the nearest old point.
                gaps: Any = ((shape[:, None, :] - strokes.local[None, first:last]) ** 2).sum(axis=2)
                nearest: Any = gaps.argmin(axis=1).astype(np.int64) + first
                result.append(Piece(stroke, nearest, nearest, np.zeros(len(shape)), False, shape))
            continue
        spans: list[Span] | None = plans.get(stroke)
        if spans is None:
            whole: Any = np.arange(first, last, dtype=np.int64)
            result.append(Piece(stroke, whole, whole, np.zeros(last - first), False))
            continue
        span: Span
        for span in spans:
            a: Any
            b: Any
            t: Any
            a, b, t = span_points(span, last - first, bool(strokes.cyclic[stroke]))
            if len(a):
                result.append(Piece(stroke, a + first, b + first, t, bool(strokes.cyclic[stroke])))
    return result


@dataclass
class Faces:
    """A mesh's faces as arrays, with 3D Paint's per-vertex ink when it has it."""

    co: Any
    starts: Any
    totals: Any
    loops: Any
    color: Any
    materials: Any

    def face(self, index: int) -> Any:
        start: int = int(self.starts[index])
        return self.loops[start : start + int(self.totals[index])]

    def pick(self, mask: Any) -> tuple[Any, Any, Any]:
        """The chosen faces' loop vertex indices, sizes, and materials."""
        chosen: Any = np.flatnonzero(mask)
        sizes: Any = self.totals[chosen].astype(np.int64)
        firsts: Any = np.concatenate(([0], np.cumsum(sizes)[:-1])) if len(sizes) else np.zeros(0, dtype=np.int64)
        steps: Any = np.arange(int(sizes.sum())) - np.repeat(firsts, sizes)
        return self.loops[np.repeat(self.starts[chosen].astype(np.int64), sizes) + steps], sizes, self.materials[chosen]


def _read_faces(mesh: Any) -> Faces:
    co: Any = np.zeros(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", co)
    count: int = len(mesh.polygons)
    starts: Any = np.zeros(count, dtype=np.int32)
    totals: Any = np.zeros(count, dtype=np.int32)
    materials: Any = np.zeros(count, dtype=np.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    mesh.polygons.foreach_get("loop_total", totals)
    mesh.polygons.foreach_get("material_index", materials)
    loops: Any = np.zeros(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loops)
    color: Any = None
    ink: Any = mesh.color_attributes.get("Ink")
    if ink is not None and str(ink.domain) == "POINT" and len(ink.data) == len(mesh.vertices):
        color = np.zeros(len(mesh.vertices) * 4, dtype=np.float32)
        ink.data.foreach_get("color", color)
        color = color.reshape(-1, 4).astype(np.float64)
    return Faces(co.reshape(-1, 3).astype(np.float64), starts, totals, loops, color, materials)


def _write_faces(obj: Any, co: Any, loops: Any, sizes: Any, color: Any, materials: Any) -> None:
    """Replace a mesh's faces, given as loop vertex indices and sizes, dropping vertices no face uses.

    Shading stays flat, as paint and fills are.
    """
    mesh: Any = obj.data
    mesh.clear_geometry()
    if not len(sizes):
        mesh.update()
        return
    flat: Any = np.asarray(loops, dtype=np.int64)
    used: Any
    remap: Any
    used, remap = np.unique(flat, return_inverse=True)
    counts: Any = np.asarray(sizes, dtype=np.int64)
    starts: Any = np.concatenate(([0], np.cumsum(counts)[:-1]))
    mesh.vertices.add(len(used))
    mesh.vertices.foreach_set("co", np.ascontiguousarray(co[used], dtype=np.float32).ravel())
    mesh.loops.add(len(flat))
    mesh.loops.foreach_set("vertex_index", remap.astype(np.int32))
    mesh.polygons.add(len(counts))
    mesh.polygons.foreach_set("loop_start", starts.astype(np.int32))
    mesh.update(calc_edges=True)
    if color is not None:
        ink: Any = mesh.color_attributes.get("Ink") or mesh.color_attributes.new(
            name="Ink", type="FLOAT_COLOR", domain="POINT"
        )
        ink.data.foreach_set("color", np.ascontiguousarray(color[used], dtype=np.float32).ravel())
    mesh.polygons.foreach_set("material_index", np.asarray(materials, dtype=np.int32))
    mesh.polygons.foreach_set("use_smooth", np.zeros(len(counts), dtype=bool))
    mesh.update()


def _ink_levels(color: Any, loops: Any, sizes: Any) -> Any:
    """3D Paint's material slot for each face from its vertices' ink, as tools/drawing.py assigns them."""
    counts: Any = np.asarray(sizes, dtype=np.int64)
    if not len(counts):
        return np.zeros(0, dtype=np.int32)
    firsts: Any = np.concatenate(([0], np.cumsum(counts)[:-1]))
    means: Any = np.add.reduceat(color[np.asarray(loops, dtype=np.int64), 3], firsts) / np.maximum(counts, 1)
    return np.clip(np.round(means * 15.0), 0, 15).astype(np.int32)


def remove_object(obj: Any) -> None:
    """Delete a stroke object with its mesh and materials once nothing else uses them."""
    if printer.is_print(obj):
        printer.forget(obj)
    mesh: Any = obj.data
    materials: list[Any] = list(mesh.materials) if mesh is not None else []
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    material: Any
    for material in materials:
        if material is not None and material.users == 0:
            bpy.data.materials.remove(material)


def _split(obj: Any, count: int) -> list[Any]:
    """The object and `count - 1` copies of it in the same collections, for the pieces of a cut stroke."""
    result: list[Any] = [obj]
    _index: int
    for _index in range(count - 1):
        copy: Any = obj.copy()
        copy.data = obj.data.copy()
        owner: Any
        for owner in obj.users_collection:
            owner.objects.link(copy)
        result.append(copy)
    return result


def _line_samples(line: list[Sample], span: Span) -> list[Sample]:
    a: Any
    b: Any
    t: Any
    a, b, t = span_points(span, len(line), False)
    return [line[int(i)].blend(line[int(j)], float(share)) for i, j, share in zip(a, b, t, strict=True)]


@dataclass
class Tube:
    """A 3D Extruder tube's centre line in its object's space and in the world."""

    local: list[Vec3]
    radius: float
    world: Any


def _tube(obj: Any) -> Tube | None:
    count: int = len(obj.data.vertices)
    co: Any = np.zeros(count * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", co)
    found: tuple[list[Vec3], float] | None = extrusion.tube_line(co)
    if found is None:
        return None
    return Tube(found[0], found[1], _transform(_matrix(obj.matrix_world), found[0]))


def _write_tube(obj: Any, points: list[Vec3], radius: float) -> None:
    vertices: Any
    loops: Any
    starts: Any
    vertices, loops, starts = extrusion.tube_mesh(points, radius)
    printer.fill_mesh(obj.data, vertices, loops, starts)


def _nearest(points: Any, line: Any) -> Any:
    """Each point's parameter on a polyline (P, 3), measured in its points."""
    count: int = len(line)
    targets: Any = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if count < 2:
        return np.zeros(len(targets))
    a: Any = line[:-1]
    d: Any = line[1:] - a
    lengths: Any = np.maximum((d * d).sum(axis=1), 1e-30)
    result: Any = np.zeros(len(targets))
    start: int
    for start in range(0, len(targets), 512):
        chunk: Any = targets[start : start + 512]
        t: Any = np.clip(((chunk[:, None, :] - a[None]) * d[None]).sum(axis=2) / lengths[None], 0.0, 1.0)
        gaps: Any = ((a[None] + d[None] * t[:, :, None] - chunk[:, None, :]) ** 2).sum(axis=2)
        best: Any = gaps.argmin(axis=1)
        result[start : start + 512] = best + t[np.arange(len(chunk)), best]
    return result


def _touching(
    samples: Any, pixels: Any, depth: Any, spread: Any, reach: float, closed: bool
) -> dict[int, tuple[float, float]]:
    """Samples a polyline's band covers: each one's depth and parameter at the closest point there.

    The band is `spread` pixels either side of the line at each point, and `reach` more.
    """
    count: int = len(pixels)
    found: dict[int, tuple[float, float]] = {}
    if not count:
        return found
    a: Any = np.arange(count if closed else max(count - 1, 1))
    b: Any = (a + 1) % count if closed else np.minimum(a + 1, count - 1)
    pa: Any = pixels[a]
    d: Any = pixels[b] - pa
    lengths: Any = np.maximum((d * d).sum(axis=1), 1e-12)
    t: Any = np.clip(((samples[:, None, :] - pa[None]) * d[None]).sum(axis=2) / lengths[None], 0.0, 1.0)
    gaps: Any = np.sqrt(((pa[None] + d[None] * t[:, :, None] - samples[:, None, :]) ** 2).sum(axis=2))
    width: Any = spread[a][None] + (spread[b] - spread[a])[None] * t + reach
    covered: Any = gaps <= width
    sample: int
    for sample in np.flatnonzero(covered.any(axis=1)):
        choices: Any = np.where(covered[sample], gaps[sample], np.inf)
        segment: int = int(choices.argmin())
        share: float = float(t[sample, segment])
        level: float = float(depth[a[segment]] + (depth[b[segment]] - depth[a[segment]]) * share)
        found[int(sample)] = (level, float(a[segment]) + share)
    return found


@dataclass
class Line:
    """A 3D Paint stroke's, pipe's, or tube's centre line as the view sees it, so thin ones are found between samples.

    `depth` is the front of the stroke: its centre's depth less its radius.
    """

    key: str
    rank: int
    erasable: bool
    pixels: Any
    depth: Any
    reach: Any
    low: Any
    high: Any


class Eraser:
    """One erase drag or Trim gesture: the view, the strokes it sees, and whether anything changed."""

    def __init__(self, context: Any, area: Any, region: Any, mode: Mode) -> None:
        self.context: Any = context
        self.area: Any = area
        self.region: Any = region
        self.view: View = View(region, area.spaces.active.region_3d)
        self.mode: Mode = mode
        self.kinds: tuple[str, ...] = KINDS[mode]
        self.strength: float = 1.0
        self.hardness: float = 0.0
        self.changed: bool = False
        self.erased: int = 0
        self.held: set[str] = set()
        self.strokes: dict[str, Strokes] = {}
        self.lines: dict[str, Line] = {}
        # Mesh strokes changed, split, or deleted since their centre lines were read.
        self.dirty: set[str] = set()
        self.boxes: list[tuple[float, float, float, float]] = []
        self.samples: Any = np.zeros((0, 2))
        self.fronts: list[set[str]] = []
        self._load()

    # What the view sees.

    def _load(self) -> None:
        self.strokes.clear()
        self.lines.clear()
        self.boxes.clear()
        obj: Any
        for obj in self.context.view_layer.objects:
            if not obj.visible_get():
                continue
            if obj.type == "GREASEPENCIL":
                index: int
                layer: Any
                for index, layer in enumerate(obj.data.layers):
                    frame: Any = layer.current_frame()
                    if layer.hide or frame is None:
                        continue
                    found: Strokes = _strokes(self.context, self.view, obj, index, layer, frame.drawing)
                    self.strokes[found.prefix] = found
            elif obj.type == "MESH" and not printer.ignored(obj, ""):
                line: Line | None = self._line(obj)
                if line is not None:
                    self.lines[line.key] = line
                corners: Any = _transform(_matrix(obj.matrix_world), [tuple(corner) for corner in obj.bound_box])
                pixels: Any
                valid: Any
                pixels, _depth, valid = self.view.project(corners)
                if valid.all():
                    low: Any = pixels.min(axis=0)
                    high: Any = pixels.max(axis=0)
                    self.boxes.append((float(low[0]), float(low[1]), float(high[0]), float(high[1])))
                else:
                    self.boxes.append((-math.inf, -math.inf, math.inf, math.inf))

    def _line(self, obj: Any) -> Line | None:
        """A paint stroke's, pipe's, or tube's centre line, or None for other objects."""
        kind: str = self._kind(obj)
        positions: Any
        radius: Any
        if kind in ("paint", "pipe"):
            line: list[Sample] = pipe_edit.samples(obj)
            if not line:
                return None
            positions = np.array([sample.position for sample in line], dtype=np.float64)
            radius = np.array([sample.radius for sample in line], dtype=np.float64)
        elif kind == "extrusion":
            tube: Tube | None = _tube(obj)
            if tube is None:
                return None
            positions = tube.world
            radius = np.full(len(positions), tube.radius * _scale(_matrix(obj.matrix_world)))
        else:
            return None
        pixels: Any
        depth: Any
        valid: Any
        pixels, depth, valid = self.view.project(positions)
        if not valid.all():
            # Partly behind the camera: rays alone find it.
            return None
        size: Any = self.view.unit * np.maximum(depth, 1e-9) if self.view.perspective else self.view.unit
        reach: Any = np.maximum(radius / size, 1.0)
        erasable: bool = kind in self.kinds and not layers.object_locked(self.context, obj)
        return Line(
            f"mesh:{obj.name}",
            RANKS[kind],
            erasable,
            pixels,
            depth - radius,
            reach,
            (pixels - reach[:, None]).min(axis=0),
            (pixels + reach[:, None]).max(axis=0),
        )

    def _touch(self, obj: Any) -> None:
        """Note a mesh stroke changed, so its centre line is read again."""
        self.dirty.add(str(obj.name))
        self.changed = True

    def _remove(self, obj: Any) -> None:
        """Delete a mesh stroke and count it as erased."""
        self.dirty.add(str(obj.name))
        remove_object(obj)
        self.erased += 1
        self.changed = True

    def _refresh(self) -> None:
        name: str
        for name in self.dirty:
            self.lines.pop(f"mesh:{name}", None)
            obj: Any = bpy.data.objects.get(name)
            if obj is not None and obj.visible_get():
                line: Line | None = self._line(obj)
                if line is not None:
                    self.lines[line.key] = line
        self.dirty.clear()

    def _reload(self, strokes: Strokes, origin: Any) -> None:
        self.strokes[strokes.prefix] = _strokes(
            self.context, self.view, strokes.obj, strokes.index, strokes.layer, strokes.drawing, origin
        )
        strokes.obj.data.update_tag()
        self.changed = True

    def _meshes_near(self, low: Any, high: Any) -> bool:
        box: tuple[float, float, float, float]
        for box in self.boxes:
            if box[0] <= high[0] and box[2] >= low[0] and box[1] <= high[1] and box[3] >= low[1]:
                return True
        return False

    def _kind(self, obj: Any) -> str:
        kind: object = obj.get("drawing_kind")
        return str(kind) if isinstance(kind, str) and kind in MESH_KINDS else ""

    def _mesh_layer(self, obj: Any, depth: float) -> Layer:
        kind: str = self._kind(obj)
        slack: float = self.view.slack(depth)
        if not kind:
            return Layer(f"other:{obj.name}", depth, OTHER_RANK, slack, False)
        editable: bool = kind in self.kinds and not layers.object_locked(self.context, obj)
        return Layer(f"mesh:{obj.name}", depth, RANKS[kind], slack, editable)

    def _hits(self, point: Vec2) -> list[tuple[Any, float, Any]]:
        """The first objects along the view ray through a pixel, nearest first: (object, depth, location)."""
        origin: Any
        direction: Any
        origin, direction = self.view.ray(point)
        depsgraph: Any = self.context.evaluated_depsgraph_get()
        found: list[tuple[Any, float, Any]] = []
        seen: set[str] = set()
        _attempt: int
        for _attempt in range(8):
            result: Any = self.context.scene.ray_cast(depsgraph, origin, direction)
            if not result[0]:
                break
            location: Any = result[1]
            obj: Any = result[4].original
            origin = location + direction * max(1e-5, 1e-6 * float(location.length))
            if printer.ignored(obj, "") or obj.name in seen:
                continue
            if self._kind(obj) == "fill" and fills.color(obj)[3] <= 0.0:
                continue
            seen.add(obj.name)
            found.append((obj, float(self.view.depth(tuple(location))[0]), location))
            if len(found) >= HITS:
                break
        return found

    def _cover(self, samples: Any, reach: float) -> list[list[tuple[Layer, float]]]:
        """Grease Pencil strokes at each sample, as layers with their parameter there (-1 inside a fill)."""
        result: list[list[tuple[Layer, float]]] = [[] for _sample in range(len(samples))]
        if not len(samples):
            return result
        low: Any = samples.min(axis=0) - reach
        high: Any = samples.max(axis=0) + reach
        strokes: Strokes
        for strokes in self.strokes.values():
            if not len(strokes.bounds):
                continue
            bounds: Any = strokes.bounds
            near: Any = (bounds[:, 0] <= high[0]) & (bounds[:, 2] >= low[0]) & (bounds[:, 1] <= high[1])
            near &= bounds[:, 3] >= low[1]
            stroke: int
            for stroke in np.flatnonzero(near):
                if not strokes.line[stroke] and not strokes.fill[stroke]:
                    continue
                first: int
                last: int
                first, last = strokes.points(int(stroke))
                pixels: Any = strokes.pixels[first:last]
                if not strokes.valid[first:last].all():
                    continue
                depth: Any = strokes.depth[first:last]
                spread: Any = strokes.reach[first:last]
                erasable: bool = strokes.editable and not strokes.locked[stroke] and "grease" in self.kinds
                key: str = strokes.key(int(stroke))
                found: dict[int, tuple[float, float]] = {}
                if strokes.line[stroke]:
                    closed: bool = bool(strokes.cyclic[stroke]) and last - first > 2
                    found = _touching(samples, pixels, depth, spread, reach, closed)
                if strokes.fill[stroke]:
                    within: Any = inside(samples, [(float(x), float(y)) for x, y in pixels])
                    level_fill: float = float(depth.mean())
                    sample_index: int
                    for sample_index in np.flatnonzero(within):
                        if int(sample_index) not in found:
                            found[int(sample_index)] = (level_fill, -1.0)
                parameter: float
                level_found: float
                for sample_index, (level_found, parameter) in found.items():
                    layer: Layer = Layer(
                        key,
                        level_found,
                        RANKS["grease"],
                        self.view.slack(level_found),
                        erasable,
                        strokes.group,
                        (strokes.index, int(stroke)),
                        strokes.flat,
                    )
                    result[sample_index].append((layer, parameter))
        mesh: Line
        for mesh in self.lines.values():
            if mesh.low[0] > high[0] or mesh.high[0] < low[0] or mesh.low[1] > high[1] or mesh.high[1] < low[1]:
                continue
            touched: dict[int, tuple[float, float]] = _touching(
                samples, mesh.pixels, mesh.depth, mesh.reach, reach, False
            )
            level_line: float
            parameter_line: float
            for sample_index, (level_line, parameter_line) in touched.items():
                result[sample_index].append(
                    (
                        Layer(mesh.key, level_line, mesh.rank, self.view.slack(level_line), mesh.erasable),
                        parameter_line,
                    )
                )
        return result

    def stacks(
        self, samples: Any, reach: float
    ) -> tuple[list[list[Layer]], list[dict[str, float]], list[dict[str, Any]]]:
        """Each sample's layers, its strokes' parameters there, and where its ray meets each object."""
        self._refresh()
        covered: list[list[tuple[Layer, float]]] = self._cover(samples, reach)
        stacks: list[list[Layer]] = []
        parameters: list[dict[str, float]] = []
        locations: list[dict[str, Any]] = []
        meshes: bool = bool(len(samples)) and self._meshes_near(samples.min(axis=0) - 1, samples.max(axis=0) + 1)
        index: int
        for index in range(len(samples)):
            stack: list[Layer] = []
            where: dict[str, float] = {}
            hits: dict[str, Any] = {}
            layer: Layer
            parameter: float
            for layer, parameter in covered[index]:
                stack.append(layer)
                where[layer.key] = parameter
            if meshes:
                obj: Any
                depth: float
                location: Any
                for obj, depth, location in self._hits((float(samples[index, 0]), float(samples[index, 1]))):
                    found: Layer = self._mesh_layer(obj, depth)
                    stack = [item for item in stack if item.key != found.key]
                    stack.append(found)
                    hits[found.key] = location
            stacks.append(stack)
            parameters.append(where)
            locations.append(hits)
        return stacks, parameters, locations

    def _bites(self, key: str, center: Vec2, radius: float) -> bool:
        """Whether the eraser can remove part of a target here; a stroke merely touching the circle cannot."""
        if self.mode == "STROKE":
            return True
        ones: Any
        if key.startswith("gp:"):
            prefix: str
            origin: str
            prefix, origin = key.rsplit("#", 1)
            strokes: Strokes | None = self.strokes.get(prefix)
            if strokes is None:
                return False
            stroke: int
            for stroke in strokes.members(int(origin)):
                first: int
                last: int
                first, last = strokes.points(stroke)
                pixels: Any = strokes.pixels[first:last]
                if strokes.fill[stroke] and not strokes.line[stroke]:
                    outline: Polygon = [(float(x), float(y)) for x, y in pixels]
                    if inside(np.array([center]), outline)[0]:
                        return True
                ones = np.ones(last - first, dtype=bool)
                if erase_spans(pixels, center, radius, ones, bool(strokes.cyclic[stroke])) is not None:
                    return True
            return False
        line: Line | None = self.lines.get(key)
        if line is None:
            # Fills, prints, and paint without a centre line are under the circle wherever a sample finds them.
            return True
        ones = np.ones(len(line.pixels), dtype=bool)
        # Pipes and tubes are cut where the circle crosses their centre line; paint loses faces out to its edge.
        reach: float = float(line.reach.max()) if line.rank == RANKS["paint"] else 0.0
        return erase_spans(line.pixels, center, radius + reach, ones, False) is not None

    def _visible(self, key: str, points: Any) -> Any:
        """Which points are nearest a sample where `key` is in front."""
        targets: Any = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        if not len(targets) or not len(self.samples):
            return np.zeros(len(targets), dtype=bool)
        allowed: Any = np.array([key in front for front in self.fronts], dtype=bool)
        result: Any = np.zeros(len(targets), dtype=bool)
        start: int
        for start in range(0, len(targets), 4096):
            chunk: Any = targets[start : start + 4096]
            gaps: Any = ((chunk[:, None, :] - self.samples[None]) ** 2).sum(axis=2)
            result[start : start + 4096] = allowed[gaps.argmin(axis=1)]
        return result

    # Erasing.

    def dab(self, center: Vec2, radius: float) -> None:
        """Erase under one dab of the eraser's circle, front-most first."""
        steps: int = max(3, min(GRID, math.ceil(2.0 * radius / 3.0)))
        offsets: Any = np.linspace(-radius, radius, steps)
        gx: Any
        gy: Any
        gx, gy = np.meshgrid(offsets, offsets)
        grid: Any = np.stack((gx.ravel(), gy.ravel()), axis=1)
        grid = grid[(grid**2).sum(axis=1) <= radius * radius + 1e-9]
        samples: Any = np.concatenate(([[0.0, 0.0]], grid)) + np.asarray(center, dtype=np.float64)
        spacing: float = 2.0 * radius / max(steps - 1, 1)
        stacks: list[list[Layer]]
        locations: list[dict[str, Any]]
        stacks, _parameters, locations = self.stacks(samples, spacing * 0.71 + 0.5)
        active: set[str]
        covered: set[str]
        bites: dict[str, bool] = {}

        def blocks(key: str) -> bool:
            # Only what this dab can remove part of holds back what it covers.
            if key not in bites:
                bites[key] = self._bites(key, center, radius)
            return bites[key]

        active, self.fronts, covered = resolve(stacks, blocks)
        # What was behind something erasable at any time in this drag waits for the next one.
        self.held |= covered
        active -= self.held
        self.samples = samples
        if not active:
            return
        grease: dict[str, list[int]] = {}
        meshes: list[str] = []
        key: str
        for key in sorted(active):
            if key.startswith("gp:"):
                prefix: str
                origin: str
                prefix, origin = key.rsplit("#", 1)
                owner: Strokes | None = self.strokes.get(prefix)
                if owner is not None:
                    grease.setdefault(prefix, []).extend(owner.members(int(origin)))
            elif key.startswith("mesh:"):
                meshes.append(key)
        strokes_prefix: str
        chosen: list[int]
        for strokes_prefix, chosen in grease.items():
            found: Strokes | None = self.strokes.get(strokes_prefix)
            if found is not None:
                self._grease(found, chosen, center, radius)
        for key in meshes:
            obj: Any = bpy.data.objects.get(key.removeprefix("mesh:"))
            if obj is None:
                continue
            kind: str = self._kind(obj)
            if self.mode == "STROKE":
                self._remove(obj)
            elif self.mode == "SOFT" and kind == "paint":
                self._fade_paint(obj, key, center, radius)
            elif self.mode == "HARD" and kind in ("paint", "fill"):
                self._clip(obj, key, kind, center, radius)
            elif self.mode == "HARD" and kind in ("pipe", "extrusion"):
                self._cut_line(obj, key, kind, center, radius)
            elif self.mode == "HARD" and kind == "print":
                self._dig(obj, key, locations, spacing)

    def _grease(self, strokes: Strokes, chosen: list[int], center: Vec2, radius: float) -> None:
        if self.mode == "STROKE":
            strokes.drawing.remove_strokes(indices=sorted(chosen))
            self.erased += len(chosen)
            self._reload(strokes, np.delete(strokes.origin, sorted(chosen)))
            return
        plans: dict[int, list[Span]] = {}
        stroke: int
        if self.mode == "SOFT":
            total: int = int(strokes.offsets[-1])
            opacity: Any = _read(strokes.drawing.attributes, "opacity", total, 1, np.float32, 1.0).astype(np.float64)
            touched: bool = False
            for stroke in chosen:
                first: int
                last: int
                first, last = strokes.points(stroke)
                pixels: Any = strokes.pixels[first:last]
                gaps: Any = np.hypot(pixels[:, 0] - center[0], pixels[:, 1] - center[1])
                acting: Any = strokes.valid[first:last] & (gaps < radius) & self._visible(strokes.key(stroke), pixels)
                if not acting.any():
                    continue
                amount: Any = self.strength * FADE * falloff(gaps, radius, self.hardness)
                values: Any = opacity[first:last]
                values[acting] = np.maximum(values[acting] - amount[acting], 0.0)
                touched = True
                spans: list[Span] | None = mask_spans(values > FADE_LIMIT, bool(strokes.cyclic[stroke]))
                if spans is not None:
                    plans[stroke] = spans
            if not touched:
                return
            attribute: Any = strokes.drawing.attributes.get("opacity") or strokes.drawing.attributes.new(
                "opacity", "FLOAT", "POINT"
            )
            attribute.data.foreach_set("value", opacity.astype(np.float32))
            origin: Any = strokes.origin
            if plans:
                origin = _rewrite(strokes, _pieces(strokes, plans))
            self._reload(strokes, origin)
            return
        shapes: dict[int, list[Any]] = {}
        cutter: Polygon = circle(center, radius)
        for stroke in chosen:
            first, last = strokes.points(stroke)
            pixels = strokes.pixels[first:last]
            if strokes.fill[stroke] and not strokes.line[stroke]:
                # A fill without a visible outline loses the area under the circle, not just its outline points.
                cut: list[Any] | None = self._fill_cut(strokes, stroke, cutter)
                if cut is not None:
                    shapes[stroke] = cut
                continue
            erasable: Any = strokes.valid[first:last] & self._visible(strokes.key(stroke), pixels)
            found: list[Span] | None = erase_spans(pixels, center, radius, erasable, bool(strokes.cyclic[stroke]))
            if found is not None:
                plans[stroke] = found
        if plans or shapes:
            self._reload(strokes, _rewrite(strokes, _pieces(strokes, plans, shapes)))

    def _fill_cut(self, strokes: Strokes, stroke: int, cutter: Polygon) -> list[Any] | None:
        """A Grease Pencil fill's outlines after the circle's area is taken out, in drawing space.

        Returns None when the circle misses the fill, and an empty list when it covers all of it.
        """
        first: int
        last: int
        first, last = strokes.points(stroke)
        pixels: Any = strokes.pixels[first:last]
        world: Any = strokes.world[first:last]
        normal: Any = np.cross(world, np.roll(world, -1, axis=0)).sum(axis=0)
        if float(np.linalg.norm(normal)) <= 1e-12:
            return None
        count: int = len(pixels)
        corners: list[Any] = [mathutils.Vector((float(x), float(y))) for x, y in pixels]
        corners += [mathutils.Vector(point) for point in cutter]
        faces: list[list[int]] = [list(range(count)), list(range(count, count + len(cutter)))]
        result: Any = mathutils.geometry.delaunay_2d_cdt(corners, [], faces, 1, 1e-4, True)
        kept: list[tuple[int, int, int]] = []
        overlap: bool = False
        face: Any
        owners: Any
        for face, owners in zip(result[2], result[5], strict=True):
            if 0 not in owners:
                continue
            if 1 in owners:
                overlap = True
                continue
            corner: int
            for corner in range(1, len(face) - 1):
                kept.append((int(face[0]), int(face[corner]), int(face[corner + 1])))
        if not overlap:
            return None
        points: list[Vec2] = [(float(vertex.x), float(vertex.y)) for vertex in result[0]]
        inverse: Any = np.linalg.inv(strokes.matrix)
        shapes: list[Any] = []
        loop: Polygon
        for loop in outlines(points, kept):
            spots: list[Any] | None = self._lift(loop, world.mean(axis=0), normal)
            if spots is not None:
                shapes.append(_transform(inverse, np.array(spots)))
        return shapes

    def _clip(self, obj: Any, key: str, kind: str, center: Vec2, radius: float) -> None:
        """Remove the part of a paint stroke or fill under the circle, clipping its larger faces along it."""
        faces: Faces = _read_faces(obj.data)
        if not len(faces.starts):
            return
        matrix: Any = _matrix(obj.matrix_world)
        inverse: Any = np.linalg.inv(matrix)
        world: Any = _transform(matrix, faces.co)
        pixels: Any
        valid: Any
        pixels, _depth, valid = self.view.project(world)
        cutter: Polygon = circle(center, radius)
        reduced: Any = np.minimum(faces.starts, len(faces.loops) - 1)
        corner: Any = pixels[faces.loops]
        low: Any = np.minimum.reduceat(corner, reduced, axis=0)
        high: Any = np.maximum.reduceat(corner, reduced, axis=0)
        near: Any = (low[:, 0] <= center[0] + radius) & (high[:, 0] >= center[0] - radius)
        near &= (low[:, 1] <= center[1] + radius) & (high[:, 1] >= center[1] - radius)
        if not near.any():
            return
        # Whole faces are judged together; only large faces across the circle's edge are clipped one by one.
        middle: Any = np.add.reduceat(corner, reduced, axis=0) / np.maximum(faces.totals, 1)[:, None]
        within: Any = np.hypot(corner[:, 0] - center[0], corner[:, 1] - center[1]) <= radius
        whole: Any = np.logical_and.reduceat(within, reduced)
        shown: Any = np.logical_and.reduceat(valid[faces.loops], reduced)
        small: Any = (high - low).max(axis=1) <= SMALL_FACE * radius
        centred: Any = np.hypot(middle[:, 0] - center[0], middle[:, 1] - center[1]) <= radius
        seen: Any = np.zeros(len(faces.starts), dtype=bool)
        seen[near] = self._visible(key, middle[near])
        acting: Any = near & seen & shown
        removed: set[int] = {int(index) for index in np.flatnonzero(acting & (whole | (small & centred)))}
        added: list[Any] = []
        added_materials: list[int] = []
        co: list[Any] = [faces.co]
        color: list[Any] = [faces.color] if faces.color is not None else []
        next_vertex: int = len(faces.co)
        face_index: Any
        for face_index in np.flatnonzero(acting & ~whole & ~small):
            index: int = int(face_index)
            ids: Any = faces.face(index)
            pieces: list[tuple[Any, list[Polygon]]] = []
            cut: bool = False
            corner_index: int
            for corner_index in range(1, len(ids) - 1):
                triangle: Any = np.array([ids[0], ids[corner_index], ids[corner_index + 1]])
                outline: Polygon = [(float(pixels[i, 0]), float(pixels[i, 1])) for i in triangle]
                outside: list[Polygon] | None = subtract(outline, cutter)
                if outside is None:
                    pieces.append((triangle, [outline]))
                else:
                    pieces.append((triangle, outside))
                    cut = True
            if not cut:
                continue
            removed.add(index)
            triangle_ids: Any
            parts: list[Polygon]
            for triangle_ids, parts in pieces:
                corners: Any = world[triangle_ids]
                normal: Any = np.cross(corners[1] - corners[0], corners[2] - corners[0])
                twice: float = float(np.linalg.norm(normal))
                if twice <= 1e-14:
                    continue
                part: Polygon
                for part in parts:
                    spots: list[Any] | None = self._lift(part, corners[0], normal)
                    if spots is None:
                        continue
                    vertices: list[int] = []
                    spot: Any
                    for spot in spots:
                        co.append(_transform(inverse, spot))
                        if faces.color is not None:
                            # Barycentric weights carry the paint's ink to the new vertex.
                            weights: Any = np.array(
                                [
                                    np.dot(np.cross(corners[2] - corners[1], spot - corners[1]), normal),
                                    np.dot(np.cross(corners[0] - corners[2], spot - corners[2]), normal),
                                    np.dot(np.cross(corners[1] - corners[0], spot - corners[0]), normal),
                                ]
                            ) / (twice * twice)
                            color.append((weights[:, None] * faces.color[triangle_ids]).sum(axis=0)[None])
                        vertices.append(next_vertex)
                        next_vertex += 1
                    added.append(np.array(vertices))
                    added_materials.append(int(faces.materials[index]))
        if not removed:
            return
        all_co: Any = np.concatenate(co)
        all_color: Any = np.concatenate(color) if color else None
        keep: Any = np.ones(len(faces.starts), dtype=bool)
        keep[list(removed)] = False
        loops: Any
        sizes: Any
        materials: Any
        loops, sizes, materials = faces.pick(keep)
        if added:
            new_sizes: Any = np.array([len(polygon) for polygon in added], dtype=np.int64)
            new_loops: Any = np.concatenate(added)
            new_materials: Any = (
                _ink_levels(all_color, new_loops, new_sizes)
                if all_color is not None
                else np.asarray(added_materials, dtype=np.int32)
            )
            loops = np.concatenate((loops, new_loops))
            sizes = np.concatenate((sizes, new_sizes))
            materials = np.concatenate((materials, new_materials))
        self.changed = True
        if not len(sizes):
            self._remove(obj)
            return
        _write_faces(obj, all_co, loops, sizes, all_color, materials)
        if kind == "paint":
            self._paint_samples(obj, center, radius, key)

    def _lift(self, polygon: Polygon, anchor: Any, normal: Any) -> list[Any] | None:
        """World points where the view rays through a polygon's pixels meet a face's plane; None if edge-on."""
        spots: list[Any] = []
        point: Vec2
        for point in polygon:
            origin: Any
            direction: Any
            origin, direction = self.view.ray(point)
            start: Any = np.asarray(origin, dtype=np.float64)
            heading: Any = np.asarray(direction, dtype=np.float64)
            facing: float = float(np.dot(normal, heading))
            if abs(facing) < 1e-12:
                return None
            spots.append(start + heading * (float(np.dot(normal, anchor - start)) / facing))
        return spots

    def _paint_samples(self, obj: Any, center: Vec2, radius: float, key: str) -> None:
        """Drop the samples of a paint stroke that were erased, and split it where its samples now break."""
        line: list[Sample] = pipe_edit.samples(obj)
        if not line:
            return
        positions: Any = np.array([sample.position for sample in line])
        pixels: Any
        valid: Any
        pixels, _depth, valid = self.view.project(positions)
        gaps: Any = np.hypot(pixels[:, 0] - center[0], pixels[:, 1] - center[1])
        gone: Any = valid & (gaps <= radius) & self._visible(key, pixels)
        spans: list[Span] | None = mask_spans(~gone, False)
        if spans is None:
            return
        if len(spans) <= 1:
            # Still one piece, or only its fringe is left: its faces stay; only the samples change.
            kept: list[Sample] = _line_samples(line, spans[0]) if spans else []
            store_samples(obj, self._local_samples(obj, kept))
            self._touch(obj)
            return
        self._split_paint(obj, line, spans, keep_all=True)

    def _split_paint(self, obj: Any, line: list[Sample], spans: list[Span], *, keep_all: bool) -> None:
        """Give each kept span of a paint stroke its own object, with the faces nearest it.

        With `keep_all`, faces nearest a removed part go to the nearest span; otherwise they are removed.
        """
        faces: Faces = _read_faces(obj.data)
        matrix: Any = _matrix(obj.matrix_world)
        positions: Any = np.array([sample.position for sample in line], dtype=np.float64)
        middles: Any = (
            np.stack([faces.co[faces.face(index)].mean(axis=0) for index in range(len(faces.starts))])
            if len(faces.starts)
            else np.zeros((0, 3))
        )
        where: Any = _nearest(_transform(matrix, middles), positions)
        owner: Any = np.full(len(where), -1, dtype=np.int64)
        span_index: int
        span: Span
        for span_index, span in enumerate(spans):
            owner[(where >= span[0] - 1e-6) & (where <= span[1] + 1e-6)] = span_index
        if keep_all and len(spans):
            middle_points: Any = np.array([(span[0] + span[1]) * 0.5 for span in spans])
            loose: Any = owner < 0
            owner[loose] = np.abs(where[loose][:, None] - middle_points[None]).argmin(axis=1)
        objects: list[Any] = _split(obj, len(spans)) if spans else []
        if not spans or not (owner >= 0).any():
            self._remove(obj)
            self.changed = True
            return
        target: Any
        for span_index, target in enumerate(objects):
            mine: Any = np.flatnonzero(owner == span_index)
            if not len(mine):
                self._remove(target)
                continue
            loops: Any
            sizes: Any
            materials: Any
            loops, sizes, materials = faces.pick(owner == span_index)
            _write_faces(target, faces.co, loops, sizes, faces.color, materials)
            store_samples(target, self._local_samples(target, _line_samples(line, spans[span_index])))
            self._touch(target)
        self.changed = True

    def _local_samples(self, obj: Any, values: list[Sample]) -> list[Sample]:
        """World samples in an object's space, as tools/drawing.py stores them."""
        inverse: Any = obj.matrix_world.inverted_safe()
        rotation: Any = obj.matrix_world.to_3x3().transposed()
        scale: float = max(abs(float(value)) for value in obj.matrix_world.to_scale())
        factor: float = math.sqrt(2.0) if obj.get("drawing_shape") == "square" else 1.0
        result: list[Sample] = []
        sample: Sample
        for sample in values:
            normal: Any = (rotation @ Vector(sample.normal)).normalized()
            spot: Any = inverse @ Vector(sample.position)
            result.append(
                Sample(
                    (float(spot.x), float(spot.y), float(spot.z)),
                    sample.radius / max(scale * factor, 1e-12),
                    sample.hardness,
                    sample.opacity,
                    (float(normal.x), float(normal.y), float(normal.z)),
                )
            )
        return result

    def _cut_line(self, obj: Any, key: str, kind: str, center: Vec2, radius: float) -> None:
        """Cut a pipe or tube where the circle crosses its centre line, rebuilding each remaining piece."""
        if kind == "pipe":
            line: list[Sample] = pipe_edit.samples(obj)
            if len(line) < 2:
                return
            positions: Any = np.array([sample.position for sample in line])
            pixels: Any
            valid: Any
            pixels, _depth, valid = self.view.project(positions)
            spans: list[Span] | None = erase_spans(pixels, center, radius, valid & self._visible(key, pixels), False)
            if spans is not None:
                self._replace_pipe(obj, [_line_samples(line, span) for span in spans])
            return
        tube: Tube | None = _tube(obj)
        if tube is None:
            return
        pixels, _depth, valid = self.view.project(tube.world)
        found: list[Span] | None = erase_spans(pixels, center, radius, valid & self._visible(key, pixels), False)
        if found is not None:
            self._replace_tube(obj, tube, found)

    def _replace_pipe(self, obj: Any, parts: list[list[Sample]]) -> None:
        pieces: list[list[Sample]] = [part for part in parts if len(part) >= 2]
        self.changed = True
        if not pieces:
            self._remove(obj)
            return
        target: Any
        part: list[Sample]
        for target, part in zip(_split(obj, len(pieces)), pieces, strict=True):
            pipe_edit.write_samples(target, part)
            self._touch(target)

    def _replace_tube(self, obj: Any, tube: Tube, spans: list[Span]) -> None:
        pieces: list[list[Vec3]] = []
        span: Span
        for span in spans:
            a: Any
            b: Any
            t: Any
            a, b, t = span_points(span, len(tube.local), False)
            points: list[Vec3] = []
            i: Any
            j: Any
            share: Any
            for i, j, share in zip(a, b, t, strict=True):
                p: Vec3 = tube.local[int(i)]
                q: Vec3 = tube.local[int(j)]
                s: float = float(share)
                points.append((p[0] + (q[0] - p[0]) * s, p[1] + (q[1] - p[1]) * s, p[2] + (q[2] - p[2]) * s))
            if len(points) >= 2:
                pieces.append(points)
        self.changed = True
        if not pieces:
            self._remove(obj)
            return
        target: Any
        points_piece: list[Vec3]
        for target, points_piece in zip(_split(obj, len(pieces)), pieces, strict=True):
            _write_tube(target, points_piece, tube.radius)
            self._touch(target)

    def _dig(self, obj: Any, key: str, locations: list[dict[str, Any]], spacing: float) -> None:
        """Dig a layer off a print where it is in front under the circle.

        Each sample digs a ball as wide as the samples are apart, so a dab takes off about that much under its
        circle, and dragging on digs deeper.
        """
        field: Any = printer.field_of(obj)
        if field is None:
            return
        matrix: Any = _matrix(obj.matrix_world)
        inverse: Any = np.linalg.inv(matrix)
        scale: float = max(_scale(matrix), 1e-12)
        points: list[Any] = []
        sizes: list[float] = []
        index: int
        hits: dict[str, Any]
        for index, hits in enumerate(locations):
            if key not in hits or key not in self.fronts[index]:
                continue
            location: Any = hits[key]
            depth: float = float(self.view.depth(tuple(location))[0])
            points.append(_transform(inverse, tuple(location))[0])
            sizes.append(spacing * self.view.pixel(depth) / scale)
        if not points or not field.dig(np.array(points), np.array(sizes)):
            return
        self.changed = True
        if field.volume() <= 0.0:
            self._remove(obj)
            return
        printer.store(obj, field)

    def _fade_paint(self, obj: Any, key: str, center: Vec2, radius: float) -> None:
        """Fade 3D Paint's ink under the soft eraser; faces whose ink is all gone go."""
        faces: Faces = _read_faces(obj.data)
        if faces.color is None or not len(faces.starts):
            return
        world: Any = _transform(_matrix(obj.matrix_world), faces.co)
        pixels: Any
        valid: Any
        pixels, _depth, valid = self.view.project(world)
        gaps: Any = np.hypot(pixels[:, 0] - center[0], pixels[:, 1] - center[1])
        acting: Any = valid & (gaps < radius)
        if not acting.any():
            return
        acting[acting] = self._visible(key, pixels[acting])
        if not acting.any():
            return
        amount: Any = self.strength * FADE * falloff(gaps, radius, self.hardness)
        color: Any = faces.color
        color[acting, 3] = np.maximum(color[acting, 3] - amount[acting], 0.0)
        reduced: Any = np.minimum(faces.starts, len(faces.loops) - 1)
        inked: Any = np.maximum.reduceat(color[faces.loops, 3], reduced) > FADE_LIMIT
        loops: Any
        sizes: Any
        loops, sizes, _materials = faces.pick(inked)
        self.changed = True
        if not len(sizes):
            self._remove(obj)
            return
        _write_faces(obj, faces.co, loops, sizes, color, _ink_levels(color, loops, sizes))

    # Trim.

    def trim(self, path: list[Vec2]) -> None:
        """Trim what the gesture touches in front: each touched piece between contacts, or the whole stroke."""
        samples: Any = _along(path)
        if not len(samples):
            return
        stacks: list[list[Layer]]
        parameters: list[dict[str, float]]
        locations: list[dict[str, Any]]
        stacks, parameters, locations = self.stacks(samples, TRIM_REACH)
        fronts: list[set[str]]
        _active: set[str]
        _covered: set[str]
        # Each point of the gesture touches only what is in front there.
        _active, fronts, _covered = resolve(stacks)
        touches: dict[str, list[Any]] = {}
        index: int
        front: set[str]
        for index, front in enumerate(fronts):
            key: str
            for key in front:
                if key.startswith("gp:"):
                    touches.setdefault(key, []).append(parameters[index].get(key, -1.0))
                elif key in locations[index]:
                    touches.setdefault(key, []).append(locations[index][key])
        if len(path) > 2 and math.dist(path[0], path[-1]) <= CLOSED:
            self._lasso(path, touches)
        if touches:
            self._apply_trim(touches)

    def _lasso(self, path: list[Vec2], touches: dict[str, list[Any]]) -> None:
        """Grease Pencil points inside a closed gesture touch their strokes, where they are in front."""
        strokes: Strokes
        for strokes in self.strokes.values():
            if not strokes.editable or not len(strokes.pixels):
                continue
            within: Any = strokes.valid & inside(strokes.pixels, path)
            if not within.any():
                continue
            points: Any = np.flatnonzero(within)
            stacks: list[list[Layer]]
            stacks, _parameters, _locations = self.stacks(strokes.pixels[points], 1.0)
            _active: set[str]
            fronts: list[set[str]]
            _covered: set[str]
            _active, fronts, _covered = resolve(stacks)
            stroke_of: Any = np.searchsorted(strokes.offsets, points, side="right") - 1
            position: int
            point: Any
            for position, point in enumerate(points):
                stroke: int = int(stroke_of[position])
                key: str = strokes.key(stroke)
                if key in fronts[position]:
                    touches.setdefault(key, []).append(float(int(point) - int(strokes.offsets[stroke])))

    def _lines(self, touched: set[str]) -> tuple[list[str], list[list[Sample]], list[Any]]:
        """Centre lines for finding contacts: the touched strokes and every stroke near them, in world space."""
        keys: list[str] = []
        paths: list[list[Sample]] = []
        meshes: list[Any] = []
        low: Any = np.full(3, np.inf)
        high: Any = np.full(3, -np.inf)
        candidates: list[tuple[str, list[Sample], Any]] = []
        strokes: Strokes
        for strokes in self.strokes.values():
            stroke: int
            for stroke in range(len(strokes.offsets) - 1):
                if not strokes.line[stroke] and not strokes.fill[stroke]:
                    continue
                first: int
                last: int
                first, last = strokes.points(stroke)
                if last - first < 1:
                    continue
                order: list[int] = list(range(first, last))
                if strokes.cyclic[stroke] and last - first > 2:
                    order.append(first)
                path: list[Sample] = [
                    Sample(
                        (float(strokes.world[i, 0]), float(strokes.world[i, 1]), float(strokes.world[i, 2])),
                        float(strokes.radius[i]),
                        1.0,
                        1.0,
                    )
                    for i in order
                ]
                candidates.append((strokes.key(stroke), path, None))
        obj: Any
        for obj in self.context.view_layer.objects:
            if obj.type != "MESH" or not obj.visible_get() or printer.ignored(obj, ""):
                continue
            kind: str = self._kind(obj)
            if kind in ("paint", "pipe"):
                found: list[Sample] = pipe_edit.samples(obj)
                if found:
                    candidates.append((f"mesh:{obj.name}", found, obj))
            elif kind == "extrusion":
                tube: Tube | None = _tube(obj)
                if tube is not None:
                    size: float = tube.radius * _scale(_matrix(obj.matrix_world))
                    candidates.append(
                        (
                            f"mesh:{obj.name}",
                            [Sample((float(p[0]), float(p[1]), float(p[2])), size, 1.0, 1.0) for p in tube.world],
                            obj,
                        )
                    )
        key: str
        path_found: list[Sample]
        mesh: Any
        for key, path_found, _mesh in candidates:
            if key in touched:
                points: Any = np.array([sample.position for sample in path_found])
                reach: float = max(sample.radius for sample in path_found)
                low = np.minimum(low, points.min(axis=0) - reach * 2)
                high = np.maximum(high, points.max(axis=0) + reach * 2)
        for key, path_found, mesh in candidates:
            points = np.array([sample.position for sample in path_found])
            reach = max(sample.radius for sample in path_found)
            if key in touched or (
                (points.max(axis=0) + reach >= low).all() and (points.min(axis=0) - reach <= high).all()
            ):
                keys.append(key)
                paths.append(path_found)
                meshes.append(mesh)
        return keys, paths, meshes

    def _apply_trim(self, touches: dict[str, list[Any]]) -> None:
        lines: list[str]
        paths: list[list[Sample]]
        meshes: list[Any]
        lines, paths, meshes = self._lines(set(touches))
        network: Network = build_network(paths)
        pipe_edit.confirm(network, meshes)
        cuts: dict[int, list[float]] = {}
        junction: Any
        for junction in network.junctions:
            segment_index: int
            amount: float
            for segment_index, amount in ((junction.first, junction.t), (junction.second, junction.u)):
                segment: Any = network.segments[segment_index]
                cuts.setdefault(segment.path, []).append(segment.index + amount)
        plans: dict[str, dict[int, list[Span]]] = {}
        key: str
        values: list[Any]
        for key, values in touches.items():
            if key.startswith("gp:"):
                prefix: str
                number: str
                prefix, number = key.rsplit("#", 1)
                strokes: Strokes | None = self.strokes.get(prefix)
                members: list[int] = strokes.members(int(number)) if strokes is not None else []
                if strokes is None or not members:
                    continue
                # A gesture trims strokes as they were when it began: one stroke per key.
                stroke: int = members[0]
                first: int
                last: int
                first, last = strokes.points(stroke)
                closed: bool = bool(strokes.cyclic[stroke]) and last - first > 2
                path_index: int = lines.index(key) if key in lines else -1
                touched: list[float] = [float(value) for value in values if float(value) >= 0.0]
                if (strokes.fill[stroke] and not strokes.line[stroke]) or not touched:
                    # A fill, or a stroke touched only inside its fill, is removed whole.
                    plans.setdefault(prefix, {})[stroke] = []
                    continue
                plans.setdefault(prefix, {})[stroke] = trim_spans(
                    last - first, closed, cuts.get(path_index, []), touched
                )
                continue
            obj: Any = bpy.data.objects.get(key.removeprefix("mesh:"))
            if obj is None:
                continue
            kind: str = self._kind(obj)
            if kind in ("fill", "print") or key not in lines:
                self._remove(obj)
                self.changed = True
                continue
            index: int = lines.index(key)
            line: list[Sample] = paths[index]
            positions: Any = np.array([sample.position for sample in line])
            spots: Any = np.array([tuple(value) for value in values], dtype=np.float64)
            where: list[float] = [float(value) for value in _nearest(spots, positions)]
            spans: list[Span] = trim_spans(len(line), False, cuts.get(index, []), where)
            if kind == "pipe":
                self._replace_pipe(obj, [_line_samples(line, span) for span in spans])
            elif kind == "extrusion":
                tube: Tube | None = _tube(obj)
                if tube is not None:
                    self._replace_tube(obj, tube, spans)
            else:
                self._split_paint(obj, line, spans, keep_all=False)
        prefix_key: str
        chosen: dict[int, list[Span]]
        for prefix_key, chosen in plans.items():
            found: Strokes | None = self.strokes.get(prefix_key)
            if found is None:
                continue
            self.erased += len(chosen)
            self._reload(found, _rewrite(found, _pieces(found, chosen)))


def _along(path: list[Vec2]) -> Any:
    """Points along a gesture, TRIM_STEP pixels apart."""
    points: list[Vec2] = []
    index: int
    for index in range(len(path) - 1):
        a: Vec2 = path[index]
        b: Vec2 = path[index + 1]
        steps: int = max(1, math.ceil(math.hypot(b[0] - a[0], b[1] - a[1]) / TRIM_STEP))
        step: int
        for step in range(steps):
            share: float = step / steps
            points.append((a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share))
    if path:
        points.append(path[-1])
    return np.array(points, dtype=np.float64).reshape(-1, 2)


def _brush() -> tuple[Mode, float, float, float, bool]:
    """The eraser brush's mode, radius in pixels, strength, hardness, and whether pressure sets its size."""
    brush: Any = bpy.context.scene.tool_settings.gpencil_paint.brush
    if brush is None:
        return "HARD", 20.0, 1.0, 0.0, False
    mode: str = str(brush.gpencil_settings.eraser_mode)
    chosen: Mode = "SOFT" if mode == "SOFT" else "STROKE" if mode == "STROKE" else "HARD"
    # Blender's brush size is a diameter in pixels.
    radius: float = max(1.0, float(brush.size) * 0.5 * float(bpy.context.preferences.system.pixel_size))
    return chosen, radius, float(brush.strength), float(brush.gpencil_settings.hardness), bool(brush.use_pressure_size)


def _finish(eraser: Eraser, message: str, nothing: str) -> None:
    if eraser.changed:
        pencil._undo_push(message)
        get_state().status = message
    else:
        get_state().status = nothing
    eraser.area.tag_redraw()


def _settle() -> None:
    """Prints and tubes still forming finish first, so erasing sees them whole."""
    printer.finish_now()
    extruder.finish_now()


class DRAWING_OT_erase(bpy.types.Operator):
    """Erase with the Erase tool's brush on every kind of stroke, front-most first."""

    bl_idname: str = "drawing.erase"
    bl_label: str = "Erase strokes"
    eraser: Eraser
    radius: float
    pressure: bool
    last: Vec2 | None

    def invoke(self, context: Any, event: Any) -> set[str]:
        mode: Mode
        strength: float
        hardness: float
        mode, self.radius, strength, hardness, self.pressure = _brush()
        _settle()
        self.eraser = Eraser(context, context.area, context.region, mode)
        self.eraser.strength = strength
        self.eraser.hardness = hardness
        self.last = None
        self._move(event)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _move(self, event: Any) -> None:
        region: Any = self.eraser.region
        point: Vec2 = (float(event.mouse_x - region.x), float(event.mouse_y - region.y))
        radius: float = self.radius
        if self.pressure and bool(getattr(event, "is_tablet", False)):
            radius = max(1.0, radius * max(0.0, min(1.0, float(event.pressure))))
        if self.last is None:
            self.eraser.dab(point, radius)
            self.last = point
            return
        gap: float = math.dist(self.last, point)
        spacing: float = max(1.0, radius * SPACING)
        if gap < spacing:
            return
        steps: int = math.ceil(gap / spacing)
        step: int
        for step in range(1, steps + 1):
            share: float = step / steps
            self.eraser.dab(
                (self.last[0] + (point[0] - self.last[0]) * share, self.last[1] + (point[1] - self.last[1]) * share),
                radius,
            )
        self.last = point
        if self.eraser.changed:
            self.eraser.area.tag_redraw()

    def modal(self, context: Any, event: Any) -> set[str]:
        if event.type in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE"):
            self._move(event)
            return {"RUNNING_MODAL"}
        if (event.type == "LEFTMOUSE" and event.value == "RELEASE") or event.type in (
            "ESC",
            "RIGHTMOUSE",
            "WINDOW_DEACTIVATE",
        ):
            _finish(self.eraser, "Erase strokes", "Nothing erasable in front under the eraser")
            return {"FINISHED"}
        return {"RUNNING_MODAL"}


class DRAWING_OT_trim(bpy.types.Operator):
    """Trim every kind of stroke along a drawn gesture, front-most first."""

    bl_idname: str = "drawing.trim"
    bl_label: str = "Trim strokes"
    points: list[Vec2]
    area: Any
    region: Any
    handle: Any

    def invoke(self, context: Any, event: Any) -> set[str]:
        self.points = [(float(event.mouse_x), float(event.mouse_y))]
        self.area, self.region = context.area, context.region
        self.handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_gesture, (), "WINDOW", "POST_PIXEL")
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def draw_gesture(self) -> None:
        from ..ui import paint

        if bpy.context.area == self.area:
            points: list[Vec2] = []
            point: Vec2
            for point in self.points:
                points.append((point[0] - self.region.x, point[1] - self.region.y))
            paint.line(points)

    def cancel(self, context: Any) -> None:
        bpy.types.SpaceView3D.draw_handler_remove(self.handle, "WINDOW")
        self.area.tag_redraw()

    def modal(self, context: Any, event: Any) -> set[str]:
        if event.type in ("ESC", "RIGHTMOUSE", "WINDOW_DEACTIVATE"):
            self.cancel(context)
            return {"CANCELLED"}
        if event.type in ("MOUSEMOVE", "INBETWEEN_MOUSEMOVE", "LEFTMOUSE"):
            self.points.append((float(event.mouse_x), float(event.mouse_y)))
            self.area.tag_redraw()
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self.cancel(context)
            _settle()
            with context.temp_override(area=self.area, region=self.region):
                eraser: Eraser = Eraser(bpy.context, self.area, self.region, "TRIM")
                eraser.trim([(x - self.region.x, y - self.region.y) for x, y in self.points])
                _finish(eraser, "Trim strokes", "Nothing to trim in front along the gesture")
            return {"FINISHED"}
        return {"RUNNING_MODAL"}


def trim_at(context: Any, area: Any, region: Any, obj: Any, positions: list[Vec3]) -> bool:
    """Trim a pipe, tube, or paint stroke as if the gesture touched it at these world positions."""
    eraser: Eraser = Eraser(context, area, region, "TRIM")
    touches: dict[str, list[Any]] = {f"mesh:{obj.name}": [Vector(position) for position in positions]}
    eraser._apply_trim(touches)
    return eraser.changed


CLASSES: tuple[type, ...] = (DRAWING_OT_erase, DRAWING_OT_trim)
