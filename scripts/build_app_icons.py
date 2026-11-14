"""Write the app's own toolbar icons in Blender's VCO triangle format.

Run from the repository root with any Python 3.11+:
    python scripts/build_app_icons.py [preview-folder]

- app.shape.dat (Shape tool): Blender's box-primitive "+" with an overlapping square, circle, and triangle.
- app.printer.dat (3D Printer): a nozzle, falling drops, and the mound they form.
- app.extruder.dat (3D Extruder): a nozzle, the tube hanging from it, and the coiled pile it lays.
- app.sketch.dat (Sketch Mesh): a bulging sheet drawn as its outline and the lines across it.

The format (as in app/drawing_app/assets/blender/SOURCE.txt) is "VCO\\0", width and height bytes, two padding
bytes, then x/y bytes for every vertex on a 256-unit canvas followed by RGBA bytes for every vertex. The "+"
triangles and the colour are copied from ops.gpencil.primitive_box.dat; outlines use that icon's 9-unit line
width. An optional folder receives 256 x 256 PNG previews on a dark button background.
"""

from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

Point = tuple[float, float]
Triangle = tuple[Point, Point, Point]
Color = tuple[int, int, int, int]

ROOT: Path = Path(__file__).resolve().parents[1]
ICONS: Path = ROOT / "app/drawing_app/assets/blender"
SOURCE: Path = ICONS / "ops.gpencil.primitive_box.dat"
WIDTH: float = 9.0
SEGMENTS: int = 40


def read_icon(path: Path) -> tuple[list[Triangle], Color]:
    data: bytes = path.read_bytes()
    count: int = (len(data) - 8) // 6
    points: list[Point] = []
    index: int
    for index in range(count):
        points.append((float(data[8 + index * 2]), float(data[9 + index * 2])))
    start: int = 8 + count * 2
    color: Color = (data[start], data[start + 1], data[start + 2], data[start + 3])
    triangles: list[Triangle] = []
    for index in range(0, count, 3):
        triangles.append((points[index], points[index + 1], points[index + 2]))
    return triangles, color


def ring(outer: list[Point], inner: list[Point]) -> list[Triangle]:
    """Quads between matching outer and inner outline vertices."""
    triangles: list[Triangle] = []
    index: int
    for index in range(len(outer)):
        following: int = (index + 1) % len(outer)
        triangles.append((outer[index], outer[following], inner[following]))
        triangles.append((outer[index], inner[following], inner[index]))
    return triangles


def inset(polygon: list[Point], distance: float) -> list[Point]:
    """A counter-clockwise convex polygon moved inward by `distance` along its edge normals."""
    lines: list[tuple[Point, Point]] = []
    index: int
    for index in range(len(polygon)):
        a: Point = polygon[index]
        b: Point = polygon[(index + 1) % len(polygon)]
        length: float = math.hypot(b[0] - a[0], b[1] - a[1])
        normal: Point = (-(b[1] - a[1]) / length, (b[0] - a[0]) / length)
        offset: Point = (normal[0] * distance, normal[1] * distance)
        lines.append(((a[0] + offset[0], a[1] + offset[1]), (b[0] + offset[0], b[1] + offset[1])))
    corners: list[Point] = []
    for index in range(len(lines)):
        (p, q), (r, s) = lines[index - 1], lines[index]
        d1: Point = (q[0] - p[0], q[1] - p[1])
        d2: Point = (s[0] - r[0], s[1] - r[1])
        t: float = ((r[0] - p[0]) * d2[1] - (r[1] - p[1]) * d2[0]) / (d1[0] * d2[1] - d1[1] * d2[0])
        corners.append((p[0] + d1[0] * t, p[1] + d1[1] * t))
    return corners


def polygon_outline(polygon: list[Point]) -> list[Triangle]:
    return ring(polygon, inset(polygon, WIDTH))


def circle_points(center: Point, radius: float, segments: int = SEGMENTS) -> list[Point]:
    points: list[Point] = []
    index: int
    for index in range(segments):
        angle: float = 2.0 * math.pi * index / segments
        points.append((center[0] + math.cos(angle) * radius, center[1] + math.sin(angle) * radius))
    return points


def circle_outline(center: Point, radius: float) -> list[Triangle]:
    return ring(circle_points(center, radius), circle_points(center, radius - WIDTH))


def fan(center: Point, outline: list[Point]) -> list[Triangle]:
    """A filled shape that every outline point can see from `center`."""
    triangles: list[Triangle] = []
    index: int
    for index in range(len(outline)):
        triangles.append((center, outline[index], outline[(index + 1) % len(outline)]))
    return triangles


def shape_icon(plus: list[Triangle]) -> list[Triangle]:
    """Square, circle, and triangle overlapping across the canvas, clear of the "+" in the top-left corner."""
    square: list[Point] = [(28.0, 22.0), (128.0, 22.0), (128.0, 122.0), (28.0, 122.0)]
    triangle: list[Point] = [(90.0, 98.0), (214.0, 98.0), (152.0, 232.0)]
    return plus + polygon_outline(square) + circle_outline((176.0, 80.0), 58.0) + polygon_outline(triangle)


def printer_icon() -> list[Triangle]:
    """A nozzle at the top, three drops falling from it, and the smooth mound they build on the ground."""
    nozzle: list[Point] = [(88.0, 192.0), (112.0, 160.0), (144.0, 160.0), (168.0, 192.0), (168.0, 234.0), (88.0, 234.0)]
    triangles: list[Triangle] = fan((128.0, 205.0), nozzle)
    center: Point
    for center in ((128.0, 136.0), (110.0, 110.0), (146.0, 110.0)):
        triangles += fan(center, circle_points(center, 9.0, 16))
    # A cos-squared hill: steep in the middle, with feet that run out along the ground.
    mound: list[Point] = [(232.0, 22.0)]
    step: int
    for step in range(33):
        x: float = 232.0 - step * 208.0 / 32.0
        mound.append((x, 22.0 + 64.0 * math.cos(math.pi * (x - 128.0) / 208.0) ** 2))
    mound.append((24.0, 22.0))
    return triangles + fan((128.0, 22.0), mound)


def stadium(left: float, right: float, bottom: float, top: float) -> list[Point]:
    """A rounded bar: a rectangle with half-circle ends, counter-clockwise."""
    radius: float = (top - bottom) / 2.0
    middle: float = (top + bottom) / 2.0
    points: list[Point] = []
    step: int
    for step in range(13):
        angle: float = -math.pi / 2.0 + math.pi * step / 12.0
        points.append((right - radius + math.cos(angle) * radius, middle + math.sin(angle) * radius))
    for step in range(13):
        angle = math.pi / 2.0 + math.pi * step / 12.0
        points.append((left + radius + math.cos(angle) * radius, middle + math.sin(angle) * radius))
    return points


def extruder_icon() -> list[Triangle]:
    """A nozzle at the top, the tube hanging from it, and three coils piled up like toothpaste."""
    nozzle: list[Point] = [(96.0, 234.0), (112.0, 200.0), (144.0, 200.0), (160.0, 234.0)]
    triangles: list[Triangle] = fan((128.0, 217.0), nozzle)
    strand: list[Point] = [(118.0, 112.0), (138.0, 112.0), (138.0, 204.0), (118.0, 204.0)]
    triangles += fan((128.0, 158.0), strand)
    bounds: tuple[float, float, float, float]
    for bounds in ((32.0, 224.0, 22.0, 56.0), (56.0, 200.0, 64.0, 94.0), (84.0, 172.0, 102.0, 126.0)):
        outline: list[Point] = stadium(*bounds)
        triangles += fan(((bounds[0] + bounds[1]) / 2.0, (bounds[2] + bounds[3]) / 2.0), outline)
    return triangles


def bezier(start: Point, control: Point, end: Point, steps: int = 10) -> list[Point]:
    """Points along a quadratic curve from `start` to `end`, pulled toward `control`."""
    points: list[Point] = []
    step: int
    for step in range(steps + 1):
        t: float = step / steps
        points.append(
            (
                (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t**2 * end[0],
                (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t**2 * end[1],
            )
        )
    return points


def stroke(points: list[Point], width: float = WIDTH) -> list[Triangle]:
    """A line of this width through the points, with round joins and ends."""
    triangles: list[Triangle] = []
    index: int
    for index in range(len(points) - 1):
        a: Point = points[index]
        b: Point = points[index + 1]
        length: float = math.hypot(b[0] - a[0], b[1] - a[1])
        side: Point = (-(b[1] - a[1]) / length * width / 2.0, (b[0] - a[0]) / length * width / 2.0)
        corners: list[Point] = [
            (a[0] + side[0], a[1] + side[1]),
            (b[0] + side[0], b[1] + side[1]),
            (b[0] - side[0], b[1] - side[1]),
            (a[0] - side[0], a[1] - side[1]),
        ]
        triangles += [(corners[0], corners[1], corners[2]), (corners[0], corners[2], corners[3])]
    point: Point
    for point in points:
        triangles += fan(point, circle_points(point, width / 2.0, 8))
    return triangles


def sketch_icon() -> list[Triangle]:
    """A sheet seen from above one corner: its curved outline, and one line each way bulging across it."""
    left: Point = (22.0, 96.0)
    front: Point = (136.0, 24.0)
    right: Point = (234.0, 112.0)
    back: Point = (116.0, 226.0)
    edges: list[list[Point]] = [
        bezier(left, (72.0, 44.0), front),
        bezier(front, (198.0, 52.0), right),
        bezier(right, (190.0, 190.0), back),
        bezier(back, (52.0, 178.0), left),
    ]
    triangles: list[Triangle] = []
    edge: list[Point]
    for edge in edges:
        triangles += stroke(edge)
    # Each inner line joins the middles of two facing edges and rises toward the viewer between them.
    middles: list[Point] = [edge[len(edge) // 2] for edge in edges]
    triangles += stroke(bezier(middles[0], (160.0, 150.0), middles[2]))
    triangles += stroke(bezier(middles[1], (112.0, 168.0), middles[3]))
    return triangles


def write_icon(path: Path, triangles: list[Triangle], color: Color) -> None:
    positions: bytearray = bytearray()
    triangle: Triangle
    point: Point
    for triangle in triangles:
        for point in triangle:
            positions += bytes((round(point[0]), round(point[1])))
    colors: bytes = bytes(color) * (len(triangles) * 3)
    path.write_bytes(b"VCO\x00\xff\xff\x00\x00" + bytes(positions) + colors)
    print(f"Wrote {path.relative_to(ROOT)} with {len(triangles)} triangles")


def write_preview(path: Path, triangles: list[Triangle], color: Color) -> None:
    """Rasterize with four samples per pixel; canvas y points up, image rows go down."""
    size: int = 256
    background: tuple[int, int, int] = (41, 41, 41)
    coverage: list[float] = [0.0] * (size * size)
    samples: tuple[Point, ...] = ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75))
    triangle: Triangle
    for triangle in triangles:
        (ax, ay), (bx, by), (cx, cy) = triangle
        area: float = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        if abs(area) < 1e-9:
            continue
        x: int
        y: int
        for y in range(max(0, int(min(ay, by, cy))), min(size, int(max(ay, by, cy)) + 1)):
            for x in range(max(0, int(min(ax, bx, cx))), min(size, int(max(ax, bx, cx)) + 1)):
                hits: int = 0
                sx: float
                sy: float
                for sx, sy in samples:
                    px: float = x + sx
                    py: float = y + sy
                    w0: float = ((bx - px) * (cy - py) - (cx - px) * (by - py)) / area
                    w1: float = ((cx - px) * (ay - py) - (ax - px) * (cy - py)) / area
                    if w0 >= 0.0 and w1 >= 0.0 and w0 + w1 <= 1.0:
                        hits += 1
                pixel: int = (size - 1 - y) * size + x
                coverage[pixel] = min(1.0, coverage[pixel] + hits / len(samples))
    rows: bytearray = bytearray()
    row: int
    for row in range(size):
        rows.append(0)
        column: int
        for column in range(size):
            amount: float = coverage[row * size + column]
            channel: int
            for channel in range(3):
                rows.append(round(background[channel] + (color[channel] - background[channel]) * amount))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    header: bytes = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    png: bytes = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows)))
    path.write_bytes(png + chunk(b"IEND", b""))


def main() -> None:
    box: list[Triangle]
    color: Color
    box, color = read_icon(SOURCE)
    plus: list[Triangle] = []
    triangle: Triangle
    for triangle in box:
        # The "+" sits in the top-left corner; the rest of the box icon is its square outline.
        if max(point[0] for point in triangle) <= 90.0 and min(point[1] for point in triangle) >= 160.0:
            plus.append(triangle)
    if len(plus) != 10:
        raise SystemExit(f"Expected the box icon's 10 plus-sign triangles, found {len(plus)}")
    icons: dict[str, list[Triangle]] = {
        "app.shape": shape_icon(plus),
        "app.printer": printer_icon(),
        "app.extruder": extruder_icon(),
        "app.sketch": sketch_icon(),
    }
    name: str
    triangles: list[Triangle]
    for name, triangles in icons.items():
        write_icon(ICONS / f"{name}.dat", triangles, color)
        if len(sys.argv) > 1:
            preview: Path = Path(sys.argv[1]) / f"{name}.png"
            write_preview(preview, triangles, color)
            print(f"Preview: {preview}")


if __name__ == "__main__":
    main()
