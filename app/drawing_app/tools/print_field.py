"""Height field behind the 3D Printer: material thickness in a grid of columns, each on its own floor.

Pure numpy, without Blender, so it can be tested on its own. Coordinates are the print object's local
units: column (i, j) covers x from origin_x + i * cell to the next column, and y likewise. A column's
floor is the surface its first material landed on, UNKNOWN until the printer has looked down for it.
"""

from __future__ import annotations

import math
from importlib import import_module
from typing import Any

# numpy ships with Blender's Python; editors without that interpreter see it as a dynamic module.
np: Any = import_module("numpy")

UNKNOWN: float = -1.0e30
# Grids larger than this on a side are coarsened, so long strokes stay fast.
MAX_SIDE: int = 400
# Material slides down steps steeper than its angle of repose.
REPOSE: float = math.tan(math.radians(34.0))
# Share of a too-steep step that moves to each lower neighbour in one relaxation step. With eight
# neighbours a column gives at most 80% of its excess, so it never ends up below the columns it fed.
RATE: float = 0.1
# Thinner material counts as none, relative to the column width.
THIN: float = 1e-4
# A particle spreads over at most this many columns per side when it lands.
MAX_FOOTPRINT: int = 32
# Neighbour offsets and their distance in columns: the four sides, then the diagonals. Sliding to all
# eight makes rounded mounds instead of square pyramids along the grid's axes.
SIDES: tuple[tuple[int, int, float], ...] = (
    (1, 0, 1.0),
    (-1, 0, 1.0),
    (0, 1, 1.0),
    (0, -1, 1.0),
    (1, 1, math.sqrt(2.0)),
    (1, -1, math.sqrt(2.0)),
    (-1, 1, math.sqrt(2.0)),
    (-1, -1, math.sqrt(2.0)),
)


def _shifted(values: Any, di: int, dj: int, fill: object) -> Any:
    """result[i, j] = values[i + di, j + dj], with `fill` beyond the edge."""
    result: Any = np.full_like(values, fill)
    nx: int = int(values.shape[0])
    ny: int = int(values.shape[1])
    result[max(-di, 0) : nx + min(-di, 0), max(-dj, 0) : ny + min(-dj, 0)] = values[
        max(di, 0) : nx + min(di, 0), max(dj, 0) : ny + min(dj, 0)
    ]
    return result


def _add_shifted(target: Any, values: Any, di: int, dj: int) -> None:
    """target[i + di, j + dj] += values[i, j] inside the grid."""
    nx: int = int(values.shape[0])
    ny: int = int(values.shape[1])
    target[max(di, 0) : nx + min(di, 0), max(dj, 0) : ny + min(dj, 0)] += values[
        max(-di, 0) : nx + min(-di, 0), max(-dj, 0) : ny + min(-dj, 0)
    ]


class HeightField:
    def __init__(self, origin: tuple[float, float], cell: float, height: Any, base: Any) -> None:
        self.origin: tuple[float, float] = origin
        self.cell: float = cell
        self.height: Any = height
        self.base: Any = base

    @classmethod
    def empty(cls, center: tuple[float, float], cell: float, side: int = 64) -> HeightField:
        half: float = side * cell / 2.0
        return cls(
            (center[0] - half, center[1] - half),
            cell,
            np.zeros((side, side), dtype=np.float64),
            np.full((side, side), UNKNOWN, dtype=np.float64),
        )

    @classmethod
    def restore(cls, grid: list[float], height: Any, base: Any) -> HeightField | None:
        """A field saved as grid = [origin x, origin y, cell, columns along x, columns along y]."""
        if len(grid) != 5 or not all(math.isfinite(value) for value in grid) or grid[2] <= 0.0:
            return None
        nx: int = int(grid[3])
        ny: int = int(grid[4])
        if nx <= 0 or ny <= 0 or height.size != nx * ny or base.size != nx * ny:
            return None
        return cls(
            (grid[0], grid[1]),
            grid[2],
            np.asarray(height, dtype=np.float64).reshape(nx, ny).copy(),
            np.asarray(base, dtype=np.float64).reshape(nx, ny).copy(),
        )

    def grid(self) -> list[float]:
        return [self.origin[0], self.origin[1], self.cell, float(self.shape[0]), float(self.shape[1])]

    @property
    def shape(self) -> tuple[int, int]:
        return int(self.height.shape[0]), int(self.height.shape[1])

    def known(self) -> Any:
        return self.base > UNKNOWN * 0.5

    def solid(self) -> Any:
        return self.height > self.cell * THIN

    def index(self, x: Any, y: Any) -> tuple[Any, Any]:
        """Column indices of local x and y; values outside the grid are not clipped."""
        return (
            np.floor((np.asarray(x, dtype=np.float64) - self.origin[0]) / self.cell).astype(np.int64),
            np.floor((np.asarray(y, dtype=np.float64) - self.origin[1]) / self.cell).astype(np.int64),
        )

    def center(self, i: Any, j: Any) -> tuple[Any, Any]:
        return (
            self.origin[0] + (np.asarray(i, dtype=np.float64) + 0.5) * self.cell,
            self.origin[1] + (np.asarray(j, dtype=np.float64) + 0.5) * self.cell,
        )

    def inside(self, i: Any, j: Any) -> Any:
        return (i >= 0) & (i < self.shape[0]) & (j >= 0) & (j < self.shape[1])

    def volume(self) -> float:
        return float(self.height.sum()) * self.cell * self.cell

    def bounds(self) -> tuple[int, int, int, int] | None:
        """Columns i0:i1 and j0:j1 that hold material, or None when the field is empty."""
        solid: Any = self.solid()
        rows: Any = np.flatnonzero(solid.any(axis=1))
        if not rows.size:
            return None
        columns: Any = np.flatnonzero(solid.any(axis=0))
        return int(rows[0]), int(rows[-1]) + 1, int(columns[0]), int(columns[-1]) + 1

    def extent(self) -> tuple[float, float, float, float] | None:
        """Local rectangle (x min, y min, x max, y max) that holds material, or None."""
        bounds: tuple[int, int, int, int] | None = self.bounds()
        if bounds is None:
            return None
        return (
            self.origin[0] + bounds[0] * self.cell,
            self.origin[1] + bounds[2] * self.cell,
            self.origin[0] + bounds[1] * self.cell,
            self.origin[1] + bounds[3] * self.cell,
        )

    def coarsen(self) -> None:
        """Merge 2 x 2 columns; thickness is averaged, so the volume is kept."""
        nx: int
        ny: int
        nx, ny = self.shape
        pad: tuple[tuple[int, int], tuple[int, int]] = ((0, nx % 2), (0, ny % 2))
        height: Any = np.pad(self.height, pad)
        base: Any = np.pad(self.base, pad, constant_values=UNKNOWN)
        blocks: tuple[int, int, int, int] = (height.shape[0] // 2, 2, height.shape[1] // 2, 2)
        known: Any = (base > UNKNOWN * 0.5).reshape(blocks)
        count: Any = known.sum(axis=(1, 3))
        total: Any = np.where(known, base.reshape(blocks), 0.0).sum(axis=(1, 3))
        self.height = height.reshape(blocks).mean(axis=(1, 3))
        self.base = np.where(count > 0, total / np.maximum(count, 1), UNKNOWN)
        self.cell *= 2.0

    def cover(self, xmin: float, ymin: float, xmax: float, ymax: float, margin: int = 8) -> None:
        """Grow the grid to hold this local rectangle with `margin` spare columns around it."""
        while True:
            nx: int
            ny: int
            nx, ny = self.shape
            low: tuple[Any, Any] = self.index(xmin, ymin)
            high: tuple[Any, Any] = self.index(xmax, ymax)
            i0: int = int(low[0]) - margin
            j0: int = int(low[1]) - margin
            i1: int = int(high[0]) + 1 + margin
            j1: int = int(high[1]) + 1 + margin
            if i0 >= 0 and j0 >= 0 and i1 <= nx and j1 <= ny:
                return
            # Grow by at least a quarter on each side that needs room, so growing stays rare.
            extra_i: int = max(margin, nx // 4)
            extra_j: int = max(margin, ny // 4)
            lo_i: int = 0 if i0 >= 0 else i0 - extra_i
            lo_j: int = 0 if j0 >= 0 else j0 - extra_j
            hi_i: int = nx if i1 <= nx else i1 + extra_i
            hi_j: int = ny if j1 <= ny else j1 + extra_j
            if max(hi_i - lo_i, hi_j - lo_j) > MAX_SIDE:
                self.coarsen()
                continue
            height: Any = np.zeros((hi_i - lo_i, hi_j - lo_j), dtype=np.float64)
            base: Any = np.full((hi_i - lo_i, hi_j - lo_j), UNKNOWN, dtype=np.float64)
            height[-lo_i : -lo_i + nx, -lo_j : -lo_j + ny] = self.height
            base[-lo_i : -lo_i + nx, -lo_j : -lo_j + ny] = self.base
            self.height = height
            self.base = base
            self.origin = (self.origin[0] + lo_i * self.cell, self.origin[1] + lo_j * self.cell)
            return

    def keep_margin(self, margin: int = 4) -> None:
        """Keep spare columns around the material, so it never piles up against the grid's edge."""
        extent: tuple[float, float, float, float] | None = self.extent()
        if extent is not None:
            inset: float = self.cell * 0.5
            self.cover(extent[0] + inset, extent[1] + inset, extent[2] - inset, extent[3] - inset, margin)

    def top(self, xmin: float, ymin: float, xmax: float, ymax: float) -> float | None:
        """The highest material surface over the local rectangle, or None where there is none."""
        low: tuple[Any, Any] = self.index(xmin, ymin)
        high: tuple[Any, Any] = self.index(xmax, ymax)
        i0: int = max(0, int(low[0]))
        j0: int = max(0, int(low[1]))
        i1: int = min(self.shape[0], int(high[0]) + 1)
        j1: int = min(self.shape[1], int(high[1]) + 1)
        if i0 >= i1 or j0 >= j1:
            return None
        height: Any = self.height[i0:i1, j0:j1]
        base: Any = self.base[i0:i1, j0:j1]
        mask: Any = (height > self.cell * THIN) & (base > UNKNOWN * 0.5)
        if not mask.any():
            return None
        return float(np.max((base + height)[mask]))

    def deposit(self, i: Any, j: Any, thickness: float) -> None:
        """Add `thickness` to each listed column; repeated columns add up."""
        np.add.at(self.height, (i, j), thickness)

    def footprint(self, x: Any, y: Any, size: float) -> tuple[Any, Any, int]:
        """Columns under particles `size` wide centred on local x and y: indices (unclipped) and columns per side."""
        side: int = int(min(MAX_FOOTPRINT, max(1, round(size / self.cell))))
        i: Any
        j: Any
        i, j = self.index(x, y)
        offsets: Any = np.arange(side) - (side - 1) // 2
        di: Any
        dj: Any
        di, dj = np.meshgrid(offsets, offsets, indexing="ij")
        return (
            (np.asarray(i).reshape(-1, 1) + di.ravel()).ravel(),
            (np.asarray(j).reshape(-1, 1) + dj.ravel()).ravel(),
            side,
        )

    def land(self, x: Any, y: Any, size: float, volume: float) -> None:
        """Particles of `volume` land at local x and y; each spreads evenly over the columns under it.

        A particle as wide as `size` covers about size / cell columns per side, so it adds about its own
        height there (a cube adds exactly its width), before slumping spreads it further.
        """
        i: Any
        j: Any
        side: int
        i, j, side = self.footprint(x, y, size)
        inside: Any = self.inside(i, j)
        self.deposit(i[inside], j[inside], volume / (side * side * self.cell * self.cell))

    def floors_needed(self, limit: int) -> tuple[Any, Any, Any]:
        """Columns beside material whose floor is unknown, and the height to look down from (just above it)."""
        bounds: tuple[int, int, int, int] | None = self.bounds()
        empty: tuple[Any, Any, Any] = (np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.float64))
        if bounds is None:
            return empty
        nx: int
        ny: int
        nx, ny = self.shape
        i0: int = max(0, bounds[0] - 1)
        i1: int = min(nx, bounds[1] + 1)
        j0: int = max(0, bounds[2] - 1)
        j1: int = min(ny, bounds[3] + 1)
        height: Any = self.height[i0:i1, j0:j1]
        base: Any = self.base[i0:i1, j0:j1]
        known: Any = base > UNKNOWN * 0.5
        surface: Any = np.where((height > self.cell * THIN) & known, base + height, -np.inf)
        highest: Any = np.full_like(surface, -np.inf)
        di: int
        dj: int
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di or dj:
                    highest = np.maximum(highest, _shifted(surface, di, dj, -np.inf))
        ii: Any
        jj: Any
        ii, jj = np.nonzero(np.isfinite(highest) & ~known)
        ii, jj = ii[:limit], jj[:limit]
        return ii + i0, jj + j0, highest[ii, jj] + self.cell

    def relax(self, iterations: int) -> float:
        """Let material slide off steps steeper than its angle of repose; returns the thickness moved."""
        bounds: tuple[int, int, int, int] | None = self.bounds()
        if bounds is None:
            return 0.0
        nx: int
        ny: int
        nx, ny = self.shape
        # Material spreads at most one column per step, so this window holds every change.
        pad: int = iterations + 1
        i0: int = max(0, bounds[0] - pad)
        i1: int = min(nx, bounds[1] + pad)
        j0: int = max(0, bounds[2] - pad)
        j1: int = min(ny, bounds[3] + pad)
        height: Any = self.height[i0:i1, j0:j1]
        base: Any = self.base[i0:i1, j0:j1]
        known: Any = base > UNKNOWN * 0.5
        floor: Any = np.where(known, base, 0.0)
        step: float = REPOSE * self.cell
        moved: float = 0.0
        di: int
        dj: int
        distance: float
        _distance: float
        flow: Any
        _iteration: int
        for _iteration in range(iterations):
            surface: Any = floor + height
            flows: list[Any] = []
            for di, dj, distance in SIDES:
                # Only between columns whose floors are known; others wait for the printer to find them.
                both: Any = known & _shifted(known, di, dj, False)
                # The allowed step grows with distance, so a diagonal neighbour may sit lower.
                drop: Any = surface - _shifted(surface, di, dj, 0.0) - step * distance
                flows.append(np.where(both, np.clip(drop * RATE, 0.0, None), 0.0))
            total: Any = np.zeros_like(height)
            for flow in flows:
                total += flow
            # A column cannot give more material than it holds.
            share: Any = np.where(total > height, height / np.maximum(total, 1e-300), 1.0)
            for flow in flows:
                flow *= share
            height -= total * share
            for (di, dj, _distance), flow in zip(SIDES, flows, strict=True):
                _add_shifted(height, flow, di, dj)
            moved += float((total * share).sum())
        np.maximum(height, 0.0, out=height)
        return moved

    def surface(self) -> Any:
        return np.where(self.known(), self.base + self.height, np.nan)

    def mesh(self) -> tuple[Any, Any, Any]:
        """Vertices (N, 3), loop vertex indices, and each polygon's first loop, as Blender stores a mesh.

        There is a quad per column with material and a skirt around them. Each corner is the mean surface
        of the columns around it, so the skirt slopes from the print's edge down to the floor and the
        outline lies on the floor instead of stepping up and down.
        """
        solid: Any = self.solid()
        if not solid.any():
            return np.zeros((0, 3), np.float32), np.zeros(0, np.int32), np.zeros(0, np.int32)
        grown: Any = solid.copy()
        di: int
        dj: int
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di or dj:
                    grown |= _shifted(solid, di, dj, False)
        # Every skirt column shares a corner with the material, so none of its quads lies flat on the floor.
        cells: Any = solid | (grown & self.known())
        rows_used: Any = np.flatnonzero(cells.any(axis=1))
        columns_used: Any = np.flatnonzero(cells.any(axis=0))
        i0: int = int(rows_used[0])
        i1: int = int(rows_used[-1]) + 1
        j0: int = int(columns_used[0])
        j1: int = int(columns_used[-1]) + 1
        nx: int
        ny: int
        nx, ny = self.shape
        # Surfaces of the material's columns and a ring around them; NaN outside the grid or where unknown.
        ring: Any = np.full((i1 - i0 + 2, j1 - j0 + 2), np.nan)
        a0: int = max(i0 - 1, 0)
        a1: int = min(i1 + 1, nx)
        b0: int = max(j0 - 1, 0)
        b1: int = min(j1 + 1, ny)
        ring[a0 - i0 + 1 : a1 - i0 + 1, b0 - j0 + 1 : b1 - j0 + 1] = self.surface()[a0:a1, b0:b1]
        around: Any = np.stack((ring[:-1, :-1], ring[1:, :-1], ring[:-1, 1:], ring[1:, 1:]))
        finite: Any = np.isfinite(around)
        corners: Any = np.where(finite, around, 0.0).sum(axis=0) / np.maximum(finite.sum(axis=0), 1)
        # Corners that touch material: all four of its columns' corners, one to three of the skirt's.
        filled: Any = np.zeros(ring.shape, dtype=bool)
        filled[a0 - i0 + 1 : a1 - i0 + 1, b0 - j0 + 1 : b1 - j0 + 1] = solid[a0:a1, b0:b1]
        touching: Any = filled[:-1, :-1] | filled[1:, :-1] | filled[:-1, 1:] | filled[1:, 1:]
        si: Any
        sj: Any
        si, sj = np.nonzero(cells[i0:i1, j0:j1])
        width: int = j1 - j0 + 1
        # Counter-clockwise from above: +x, then +y.
        quads: Any = np.stack(
            (si * width + sj, (si + 1) * width + sj, (si + 1) * width + sj + 1, si * width + sj + 1), axis=1
        )
        lifted: Any = touching.ravel()[quads]
        # A skirt column touching the material at one corner becomes a triangle without its far corner,
        # so stair-stepped outlines turn into straight diagonals.
        single: Any = lifted.sum(axis=1) == 1
        corner_quads: Any = quads[single]
        tips: Any = np.argmax(lifted[single], axis=1)
        rows_index: Any = np.arange(len(corner_quads))
        triangles: Any = np.stack(
            (
                corner_quads[rows_index, (tips - 1) % 4],
                corner_quads[rows_index, tips],
                corner_quads[rows_index, (tips + 1) % 4],
            ),
            axis=1,
        )
        kept: Any = quads[~single]
        loops: Any = np.concatenate((kept.ravel(), triangles.ravel()))
        starts: Any = np.concatenate((np.arange(0, kept.size, 4), kept.size + np.arange(0, triangles.size, 3)))
        used: Any
        remap: Any
        used, remap = np.unique(loops, return_inverse=True)
        rows: Any
        columns: Any
        rows, columns = np.divmod(used, width)
        vertices: Any = np.stack(
            (
                self.origin[0] + (i0 + rows) * self.cell,
                self.origin[1] + (j0 + columns) * self.cell,
                corners.ravel()[used],
            ),
            axis=1,
        ).astype(np.float32)
        return vertices, remap.ravel().astype(np.int32), starts.astype(np.int32)
