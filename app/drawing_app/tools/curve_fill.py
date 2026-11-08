"""Cached planar fill regions derived only from stroke centerlines."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..core.state import Vec3
from .geometry import Sample, add, basis, dot, length, mul, unit
from .pipe_geometry import Junction, Network, Segment, Vec2, _faces, closest, inside, sub

PLANARITY_FRACTION: float = 0.05


@dataclass
class Mesh:
    points: list[Vec3]
    faces: list[tuple[int, int, int]]


@dataclass
class Plane:
    origin: Vec3
    normal: Vec3
    right: Vec3
    up: Vec3
    extent: tuple[float, float, float, float]
    tolerance: float
    polygons: list[list[Vec2]] = field(default_factory=list)
    areas: list[float] = field(default_factory=list)
    bounds: list[tuple[float, float, float, float]] = field(default_factory=list)
    parents: list[int | None] = field(default_factory=list)
    meshes: dict[int, Mesh] = field(default_factory=dict)
    ready: bool = False


@dataclass
class Prepared:
    planes: list[Plane]
    tolerance: float
    point_count: int
    network: Network


@dataclass
class _Tree:
    minimum: Vec3
    maximum: Vec3
    indices: list[int]
    left: _Tree | None = None
    right: _Tree | None = None


def _simplify(path: list[Vec3], tolerance: float) -> list[Vec3]:
    """Drop redundant samples with a bounded, numerical-only deviation."""
    if len(path) < 3:
        return path[:]
    keep: set[int] = {0, len(path) - 1}
    stack: list[tuple[int, int]] = [(0, len(path) - 1)]
    threshold: float = tolerance * tolerance
    while stack:
        first: int
        last: int
        first, last = stack.pop()
        direction: Vec3 = sub(path[last], path[first])
        squared: float = dot(direction, direction)
        farthest: int = first
        maximum: float = threshold
        index: int
        for index in range(first + 1, last):
            delta: Vec3 = sub(path[index], path[first])
            t: float = max(0.0, min(1.0, dot(delta, direction) / squared)) if squared else 0.0
            offset: Vec3 = sub(delta, mul(direction, t))
            distance: float = dot(offset, offset)
            if distance > maximum:
                farthest, maximum = index, distance
        if farthest != first:
            keep.add(farthest)
            stack.extend(((first, farthest), (farthest, last)))
    return [path[index] for index in sorted(keep)]


def _tree(segments: list[Segment], indices: list[int], margins: list[float] | None = None) -> _Tree:
    lows: list[float] = [math.inf, math.inf, math.inf]
    highs: list[float] = [-math.inf, -math.inf, -math.inf]
    index: int
    for index in indices:
        margin: float = margins[segments[index].path] if margins is not None else 0.0
        coordinate: int
        for coordinate in range(3):
            lows[coordinate] = min(
                lows[coordinate],
                segments[index].a.position[coordinate] - margin,
                segments[index].b.position[coordinate] - margin,
            )
            highs[coordinate] = max(
                highs[coordinate],
                segments[index].a.position[coordinate] + margin,
                segments[index].b.position[coordinate] + margin,
            )
    minimum: Vec3 = (lows[0], lows[1], lows[2])
    maximum: Vec3 = (highs[0], highs[1], highs[2])
    result: _Tree = _Tree(minimum, maximum, indices if len(indices) <= 8 else [])
    if len(indices) > 8:
        spans: Vec3 = sub(maximum, minimum)
        axis: int = max(range(3), key=spans.__getitem__)

        def center(index: int) -> float:
            return segments[index].a.position[axis] + segments[index].b.position[axis]

        ordered: list[int] = sorted(indices, key=center)
        middle: int = len(ordered) // 2
        result.left, result.right = _tree(segments, ordered[:middle], margins), _tree(
            segments, ordered[middle:], margins
        )
    return result


def _network(paths: list[list[Vec3]], tolerance: float, nearby: list[float] | None = None) -> Network:
    samples: list[list[Sample]] = []
    path: list[Vec3]
    point: Vec3
    for path in paths:
        samples.append([Sample(point, 0.0, 1.0, 1.0) for point in path])
    result: Network = Network(samples)
    path_index: int
    points: list[Sample]
    for path_index, points in enumerate(samples):
        distance: float = 0.0
        index: int
        for index in range(len(points) - 1):
            size: float = length(sub(points[index + 1].position, points[index].position))
            if size > tolerance:
                result.segments.append(Segment(path_index, index, points[index], points[index + 1], distance))
            distance += size
    if not result.segments or (nearby is not None and len(paths) == 1):
        return result
    root: _Tree = _tree(result.segments, list(range(len(result.segments))), nearby)
    pairs: set[tuple[int, int]] = set()
    segment: Segment
    for index, segment in enumerate(result.segments):
        reach: float = nearby[segment.path] if nearby is not None else tolerance
        stack: list[_Tree] = [root]
        while stack:
            node: _Tree = stack.pop()
            overlaps: bool = True
            axis: int
            for axis in range(3):
                if (
                    max(segment.a.position[axis], segment.b.position[axis]) + reach < node.minimum[axis]
                    or min(segment.a.position[axis], segment.b.position[axis]) - reach > node.maximum[axis]
                ):
                    overlaps = False
                    break
            if not overlaps:
                continue
            if node.left is not None and node.right is not None:
                stack.extend((node.left, node.right))
                continue
            other_index: int
            for other_index in node.indices:
                if other_index <= index:
                    continue
                other: Segment = result.segments[other_index]
                pair: tuple[int, int] = (min(segment.path, other.path), max(segment.path, other.path))
                if nearby is not None and (segment.path == other.path or pair in pairs):
                    continue
                if segment.path == other.path and abs(segment.index - other.index) <= 1:
                    continue
                t: float
                u: float
                gap: float
                t, u, gap = closest(segment.a.position, segment.b.position, other.a.position, other.b.position)
                contact: float = math.sqrt(reach * nearby[other.path]) if nearby is not None else tolerance
                if gap <= contact:
                    point: Vec3 = mul(
                        add(segment.a.blend(segment.b, t).position, other.a.blend(other.b, u).position), 0.5
                    )
                    result.junctions.append(Junction(index, other_index, t, u, point, gap, contact))
                    pairs.add(pair)
    return result


def _span(points: list[Vec3]) -> float:
    minimum: list[float] = list(points[0])
    maximum: list[float] = list(points[0])
    point: Vec3
    axis: int
    for point in points:
        for axis in range(3):
            minimum[axis] = min(minimum[axis], point[axis])
            maximum[axis] = max(maximum[axis], point[axis])
    return math.hypot(maximum[0] - minimum[0], maximum[1] - minimum[1], maximum[2] - minimum[2])


def _fit_plane(points: list[Vec3], tolerance: float) -> tuple[Vec3, Vec3] | None:
    """Least-squares plane; retain only boundaries with bounded perpendicular error."""
    if len(points) < 3:
        return None
    reference: Vec3 = points[0]
    offsets: list[Vec3] = []
    point: Vec3
    for point in points:
        offsets.append(sub(point, reference))
    count: int = len(points)
    center: Vec3 = (
        math.fsum(point[0] for point in offsets) / count,
        math.fsum(point[1] for point in offsets) / count,
        math.fsum(point[2] for point in offsets) / count,
    )
    origin: Vec3 = add(reference, center)
    scale: float = max(_span(points), tolerance)
    covariance: list[list[float]] = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    row: int
    column: int
    for point in offsets:
        delta: Vec3 = mul(sub(point, center), 1.0 / scale)
        for row in range(3):
            for column in range(3):
                covariance[row][column] += delta[row] * delta[column] / count
    vectors: list[list[float]] = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    _iteration: int
    for _iteration in range(24):
        p: int = 0
        q: int = 1
        for row, column in ((0, 2), (1, 2)):
            if abs(covariance[row][column]) > abs(covariance[p][q]):
                p, q = row, column
        off: float = covariance[p][q]
        if abs(off) <= 1e-15:
            break
        tau: float = (covariance[q][q] - covariance[p][p]) / (2.0 * off)
        tangent: float = math.copysign(1.0, tau) / (abs(tau) + math.hypot(1.0, tau))
        cosine: float = 1.0 / math.hypot(1.0, tangent)
        sine: float = tangent * cosine
        covariance[p][p] -= tangent * off
        covariance[q][q] += tangent * off
        covariance[p][q] = covariance[q][p] = 0.0
        for row in range(3):
            if row not in (p, q):
                a: float = covariance[row][p]
                b: float = covariance[row][q]
                covariance[row][p] = covariance[p][row] = cosine * a - sine * b
                covariance[row][q] = covariance[q][row] = sine * a + cosine * b
            a = vectors[row][p]
            b = vectors[row][q]
            vectors[row][p] = cosine * a - sine * b
            vectors[row][q] = sine * a + cosine * b
    eigenvalues: list[float] = [covariance[0][0], covariance[1][1], covariance[2][2]]
    order: list[int] = sorted(range(3), key=eigenvalues.__getitem__)
    if eigenvalues[order[1]] <= max(1e-20, eigenvalues[order[2]] * 1e-12):
        return None
    column = order[0]
    normal: Vec3 = unit((vectors[0][column], vectors[1][column], vectors[2][column]))
    if any(abs(dot(sub(point, origin), normal)) > tolerance for point in points):
        return None
    return origin, normal


def _components(network: Network) -> list[list[int]]:
    parents: list[int] = list(range(len(network.paths)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    junction: Junction
    for junction in network.junctions:
        first: int = root(network.segments[junction.first].path)
        second: int = root(network.segments[junction.second].path)
        parents[second] = first
    groups: dict[int, list[int]] = {}
    index: int
    for index in range(len(parents)):
        groups.setdefault(root(index), []).append(index)
    result: list[list[int]] = []
    group: list[int]
    for group in groups.values():
        if len(group) > 1:
            result.append(group)
    return result


def _area(polygon: list[Vec2]) -> float:
    result: float = 0.0
    index: int
    point: Vec2
    for index, point in enumerate(polygon):
        result += polygon[index - 1][0] * point[1] - point[0] * polygon[index - 1][1]
    return abs(result) * 0.5


def prepare(paths: list[list[Vec3]]) -> Prepared:
    """Cache curve contacts and candidate planes once per drawing revision."""
    extent: float = 0.0
    magnitude: float = 0.0
    minimum: list[float] = [math.inf, math.inf, math.inf]
    maximum: list[float] = [-math.inf, -math.inf, -math.inf]
    path: list[Vec3]
    point: Vec3
    for path in paths:
        if path:
            for point in path:
                extent = max(extent, length(sub(point, path[0])))
                magnitude = max(magnitude, abs(point[0]), abs(point[1]), abs(point[2]))
                axis: int
                for axis in range(3):
                    minimum[axis] = min(minimum[axis], point[axis])
                    maximum[axis] = max(maximum[axis], point[axis])
    tolerance: float = max(1e-7, extent * 1e-6, magnitude * 1e-7)
    simplified: list[list[Vec3]] = []
    for path in paths:
        if len(path) < 2:
            continue
        curve: list[Vec3] = _simplify(path, tolerance)
        if len(curve) >= 3 and curve[-1] != curve[0]:
            # Close the fill boundary without changing the drawn stroke.
            curve.append(curve[0])
        simplified.append(curve)
    path_tolerances: list[float] = []
    for path in simplified:
        path_tolerances.append(max(tolerance, _span(path) * PLANARITY_FRACTION))
    network: Network = _network(simplified, tolerance, path_tolerances)
    result: Prepared = Prepared([], tolerance, sum(len(path) for path in simplified), network)
    groups: list[list[int]] = _components(network)
    component_count: int = len(groups)
    covered: set[int] = set()
    junction: Junction
    for junction in network.junctions:
        groups.append([network.segments[junction.first].path, network.segments[junction.second].path])
    index: int
    for index in range(len(simplified)):
        groups.append([index])
    group: list[int]
    group_index: int
    for group_index, group in enumerate(groups):
        if group_index >= component_count and all(index in covered for index in group):
            continue
        points: list[Vec3] = []
        for index in group:
            path = simplified[index]
            points.extend(path[:-1] if path[0] == path[-1] else path)
        plane_tolerance: float = max(tolerance, _span(points) * PLANARITY_FRACTION)
        fitted: tuple[Vec3, Vec3] | None = _fit_plane(points, plane_tolerance)
        if fitted is None:
            continue
        if group_index < component_count:
            covered.update(group)
        origin: Vec3
        normal: Vec3
        origin, normal = fitted
        duplicate: bool = False
        previous: Plane
        for previous in result.planes:
            if (
                abs(dot(previous.normal, normal)) > 0.99999
                and abs(dot(sub(previous.origin, origin), normal)) <= tolerance
                and previous.tolerance >= plane_tolerance
            ):
                duplicate = True
                break
        if duplicate:
            continue
        right: Vec3
        up: Vec3
        right, up = basis(normal)
        corners: list[Vec2] = []
        x: float
        y: float
        z: float
        for x in (minimum[0], maximum[0]):
            for y in (minimum[1], maximum[1]):
                for z in (minimum[2], maximum[2]):
                    offset: Vec3 = sub((x, y, z), origin)
                    corners.append((dot(offset, right), dot(offset, up)))
        result.planes.append(Plane(origin, normal, right, up, _bounds(corners), plane_tolerance))
    return result


def _bounds(points: list[Vec2]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    point: Vec2
    for point in points:
        xs.append(point[0])
        ys.append(point[1])
    return min(xs), min(ys), max(xs), max(ys)


def _contains(bounds: tuple[float, float, float, float], point: Vec2) -> bool:
    return bounds[0] <= point[0] <= bounds[2] and bounds[1] <= point[1] <= bounds[3]


def _prepare_plane(prepared: Prepared, plane: Plane) -> None:
    """Find faces only on a plane reached by the mouse ray."""
    if plane.ready:
        return
    paths: list[list[Vec3]] = []
    indices: list[int] = []
    path_index: int
    path: list[Sample]
    sample: Sample
    for path_index, path in enumerate(prepared.network.paths):
        if any(abs(dot(sub(sample.position, plane.origin), plane.normal)) > plane.tolerance for sample in path):
            continue
        projected: list[Vec3] = []
        for sample in path:
            distance: float = dot(sub(sample.position, plane.origin), plane.normal)
            projected.append(sub(sample.position, mul(plane.normal, distance)))
        paths.append(projected)
        indices.append(path_index)
    network: Network = _network(paths, prepared.tolerance)
    segments: dict[tuple[int, int], int] = {}
    index: int
    segment: Segment
    for index, segment in enumerate(network.segments):
        segments[(indices[segment.path], segment.index)] = index
    contacts: dict[int, list[float]] = {}
    junction: Junction
    for junction in network.junctions:
        contacts.setdefault(junction.first, []).append(junction.t)
        contacts.setdefault(junction.second, []).append(junction.u)

    def connected(index: int, amount: float) -> bool:
        segment: Segment = network.segments[index]
        epsilon: float = prepared.tolerance / length(sub(segment.b.position, segment.a.position))
        parameter: float
        for parameter in contacts.get(index, ()):
            if abs(parameter - amount) <= epsilon:
                return True
        return False

    def gap_size(junction: Junction) -> float:
        return junction.gap

    for junction in sorted(prepared.network.junctions, key=gap_size):
        original_first: Segment = prepared.network.segments[junction.first]
        original_second: Segment = prepared.network.segments[junction.second]
        first_index: int | None = segments.get((original_first.path, original_first.index))
        second_index: int | None = segments.get((original_second.path, original_second.index))
        if first_index is None or second_index is None:
            continue
        first: Segment = network.segments[first_index]
        second: Segment = network.segments[second_index]
        t: float
        u: float
        gap: float
        t, u, gap = closest(first.a.position, first.b.position, second.a.position, second.b.position)
        if prepared.tolerance < gap <= junction.radius:
            # Preserve existing corners before snapping looser gaps.
            if connected(first_index, t) and connected(second_index, u):
                continue
            contact: Vec3 = mul(add(first.a.blend(first.b, t).position, second.a.blend(second.b, u).position), 0.5)
            network.junctions.append(Junction(first_index, second_index, t, u, contact, gap, junction.radius))
            contacts.setdefault(first_index, []).append(t)
            contacts.setdefault(second_index, []).append(u)
    boundary: list[Vec3]
    point: Vec3
    for boundary in _faces(network, plane.origin, plane.normal, prepared.tolerance):
        polygon: list[Vec2] = []
        for point in boundary:
            offset: Vec3 = sub(point, plane.origin)
            polygon.append((dot(offset, plane.right), dot(offset, plane.up)))
        plane.polygons.append(polygon)
        plane.areas.append(_area(polygon))
        plane.bounds.append(_bounds(polygon))
    for index, polygon in enumerate(plane.polygons):
        containing: list[int] = []
        other: int
        for other in range(len(plane.polygons)):
            if (
                plane.areas[other] > plane.areas[index]
                and _contains(plane.bounds[other], polygon[0])
                and inside(polygon[0], plane.polygons[other])
            ):
                containing.append(other)
        plane.parents.append(min(containing, key=plane.areas.__getitem__) if containing else None)
    plane.ready = True


def _triangulate(plane: Plane, index: int, tolerance: float) -> Mesh:
    """Sweep polygon edges into trapezoids; holes follow the same parity rule."""
    loops: list[list[Vec2]] = [plane.polygons[index]]
    child: int
    parent: int | None
    for child, parent in enumerate(plane.parents):
        if parent == index:
            loops.append(plane.polygons[child])
    edges: list[tuple[Vec2, Vec2]] = []
    levels: set[float] = set()
    polygon: list[Vec2]
    for polygon in loops:
        position: int
        for position in range(len(polygon)):
            a: Vec2 = polygon[position - 1]
            b: Vec2 = polygon[position]
            levels.update((a[1], b[1]))
            if a[1] != b[1]:
                edges.append((a, b) if a[1] < b[1] else (b, a))

    def edge_bottom(edge: tuple[Vec2, Vec2]) -> float:
        return edge[0][1]

    edges.sort(key=edge_bottom)
    heights: list[float] = sorted(levels)
    active: list[tuple[Vec2, Vec2]] = []
    cursor: int = 0
    result: Mesh = Mesh([], [])
    vertices: dict[Vec2, int] = {}

    def x_at(edge: tuple[Vec2, Vec2], y: float) -> float:
        a: Vec2
        b: Vec2
        a, b = edge
        return a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1])

    def vertex(point: Vec2) -> int:
        if point not in vertices:
            vertices[point] = len(result.points)
            result.points.append(add(plane.origin, add(mul(plane.right, point[0]), mul(plane.up, point[1]))))
        return vertices[point]

    for position in range(len(heights) - 1):
        bottom: float = heights[position]
        top: float = heights[position + 1]
        if top - bottom <= max(math.ulp(bottom), math.ulp(top)) * 16.0:
            continue
        middle: float = (bottom + top) * 0.5
        edge: tuple[Vec2, Vec2]
        remaining: list[tuple[Vec2, Vec2]] = []
        for edge in active:
            if edge[1][1] > middle:
                remaining.append(edge)
        active = remaining
        while cursor < len(edges) and edges[cursor][0][1] <= middle:
            if edges[cursor][1][1] > middle:
                active.append(edges[cursor])
            cursor += 1

        def crossing(edge: tuple[Vec2, Vec2], y: float = middle) -> float:
            return x_at(edge, y)

        active.sort(key=crossing)
        if len(active) % 2:
            raise ValueError("The curve boundary is not closed")
        for index in range(0, len(active), 2):
            left: tuple[Vec2, Vec2] = active[index]
            right: tuple[Vec2, Vec2] = active[index + 1]
            corners: tuple[Vec2, Vec2, Vec2, Vec2] = (
                (x_at(left, bottom), bottom),
                (x_at(right, bottom), bottom),
                (x_at(right, top), top),
                (x_at(left, top), top),
            )
            first: int
            second: int
            third: int
            for first, second, third in ((0, 1, 2), (0, 2, 3)):
                a, b = corners[first], corners[second]
                c: Vec2 = corners[third]
                area: float = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
                if area > tolerance * tolerance:
                    result.faces.append((vertex(a), vertex(b), vertex(c)))
    return result


def fill(prepared: Prepared, origin: Vec3, direction: Vec3) -> Mesh | None:
    """Pick the nearest enclosed region and reuse its triangulated surface."""
    hits: list[tuple[float, Plane, Vec2]] = []
    plane: Plane
    for plane in prepared.planes:
        denominator: float = dot(direction, plane.normal)
        if abs(denominator) <= 1e-12:
            continue
        distance: float = dot(sub(plane.origin, origin), plane.normal) / denominator
        if distance < 0.0:
            continue
        offset: Vec3 = sub(add(origin, mul(direction, distance)), plane.origin)
        point: Vec2 = (dot(offset, plane.right), dot(offset, plane.up))
        if _contains(plane.extent, point):
            hits.append((distance, plane, point))

    def hit_distance(hit: tuple[float, Plane, Vec2]) -> float:
        return hit[0]

    hits.sort(key=hit_distance)
    _distance: float
    for _distance, plane, point in hits:
        _prepare_plane(prepared, plane)
        candidates: list[int] = []
        index: int
        bounds: tuple[float, float, float, float]
        for index, bounds in enumerate(plane.bounds):
            if _contains(bounds, point):
                if inside(point, plane.polygons[index]):
                    candidates.append(index)
        if candidates:
            index = min(candidates, key=plane.areas.__getitem__)
            if index not in plane.meshes:
                plane.meshes[index] = _triangulate(plane, index, prepared.tolerance)
            result: Mesh = plane.meshes[index]
            if result.faces:
                return result
    return None
