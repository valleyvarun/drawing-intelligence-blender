"""Isolated plain-Python curve-fill computation; never imports Blender."""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import TypeAlias, cast

Vec3: TypeAlias = tuple[float, float, float]


def _packages() -> None:
    root: Path = Path(__file__).resolve().parents[1]
    name: str
    directory: Path
    for name, directory in (
        ("drawing_app", root),
        ("drawing_app.core", root / "core"),
        ("drawing_app.tools", root / "tools"),
    ):
        package: ModuleType = ModuleType(name)
        package.__path__ = [str(directory)]
        sys.modules[name] = package


def _vector(value: object) -> Vec3:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("Expected a three-dimensional coordinate")
    coordinate: object
    for coordinate in value:
        if not isinstance(coordinate, (int, float)) or not math.isfinite(coordinate):
            raise ValueError("Invalid curve coordinate")
    vector: list[float] = cast(list[float], value)
    return float(vector[0]), float(vector[1]), float(vector[2])


def _request(value: object) -> tuple[list[list[Vec3]], Vec3, Vec3]:
    if not isinstance(value, dict):
        raise ValueError("Invalid fill request")
    data: dict[str, object] = cast(dict[str, object], value)
    entries: object = data.get("paths")
    if not isinstance(entries, list):
        raise ValueError("Missing curve paths")
    paths: list[list[Vec3]] = []
    entry: object
    for entry in entries:
        if not isinstance(entry, list):
            raise ValueError("Invalid curve path")
        path: list[Vec3] = []
        point: object
        for point in entry:
            path.append(_vector(point))
        paths.append(path)
    return paths, _vector(data.get("origin")), _vector(data.get("direction"))


def run(directory: Path) -> None:
    from drawing_app.tools.curve_fill import Mesh, Prepared, fill, prepare

    paths: list[list[Vec3]] = []
    prepared: Prepared | None = None
    touched: float = time.monotonic()
    request: Path = directory / "request.json"
    while directory.is_dir() and time.monotonic() - touched < 300.0:
        if not request.is_file():
            time.sleep(0.03)
            continue
        try:
            incoming_text: str = request.read_text(encoding="utf-8")
            request.unlink()
        except (PermissionError, FileNotFoundError):
            time.sleep(0.03)
            continue
        started: float = time.monotonic()
        response: dict[str, object]
        error: Exception
        try:
            incoming: object = json.loads(incoming_text)
            current: list[list[Vec3]]
            origin: Vec3
            direction: Vec3
            current, origin, direction = _request(incoming)
            cached: bool = prepared is not None and current == paths
            if not cached:
                prepared = prepare(current)
                paths = current
            assert prepared is not None
            mesh: Mesh | None = fill(prepared, origin, direction)
            response = {
                "points": mesh.points if mesh is not None else [],
                "faces": mesh.faces if mesh is not None else [],
                "cached": cached,
                "seconds": time.monotonic() - started,
            }
        except Exception as error:
            response = {"error": str(error) or type(error).__name__}
        temporary: Path = directory / "result.tmp"
        temporary.write_text(json.dumps(response, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        temporary.replace(directory / "result.json")
        touched = time.monotonic()


if __name__ == "__main__":
    _packages()
    run(Path(sys.argv[1]))
