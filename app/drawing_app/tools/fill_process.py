"""Nonblocking transport for one isolated curve-fill worker."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO, TypeAlias, cast

Vec3: TypeAlias = tuple[float, float, float]


def _chunks(tokens: Iterator[str]) -> Iterator[str]:
    parts: list[str] = []
    size: int = 0
    token: str
    for token in tokens:
        parts.append(token)
        size += len(token)
        if size >= 4096:
            yield "".join(parts)
            parts.clear()
            size = 0
    if parts:
        yield "".join(parts)


@dataclass(frozen=True)
class FillResult:
    points: list[Vec3] = field(default_factory=list)
    faces: list[tuple[int, int, int]] = field(default_factory=list)
    error: str = ""
    cached: bool = False
    seconds: float = 0.0


def _result(value: object) -> FillResult:
    if not isinstance(value, dict):
        raise ValueError("Invalid fill worker result")
    data: dict[str, object] = cast(dict[str, object], value)
    points: list[Vec3] = []
    faces: list[tuple[int, int, int]] = []
    entries: object = data.get("points", [])
    if not isinstance(entries, list):
        raise ValueError("Invalid fill vertices")
    entry: object
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 3:
            raise ValueError("Invalid fill vertex")
        coordinate: object
        for coordinate in entry:
            if not isinstance(coordinate, (int, float)):
                raise ValueError("Invalid fill coordinate")
        vector: list[float] = cast(list[float], entry)
        points.append((float(vector[0]), float(vector[1]), float(vector[2])))
    entries = data.get("faces", [])
    if not isinstance(entries, list):
        raise ValueError("Invalid fill triangles")
    for entry in entries:
        if not isinstance(entry, list) or len(entry) != 3:
            raise ValueError("Invalid fill triangle")
        for coordinate in entry:
            if not isinstance(coordinate, int) or not 0 <= coordinate < len(points):
                raise ValueError("Invalid fill vertex index")
        triangle: list[int] = cast(list[int], entry)
        faces.append((triangle[0], triangle[1], triangle[2]))
    seconds: object = data.get("seconds", 0.0)
    return FillResult(
        points,
        faces,
        str(data.get("error", "")),
        bool(data.get("cached", False)),
        float(seconds) if isinstance(seconds, (int, float)) else 0.0,
    )


class FillProcess:
    """Only snapshots cross the process boundary; Blender stays on its main thread."""

    def __init__(self, root: Path, python: Path | None = None) -> None:
        self.root: Path = root.resolve()
        self.python: Path = python or self.root / "engine/5.2/python/bin/python.exe"
        self._process: subprocess.Popen[bytes] | None = None
        self._directory: Path | None = None
        self._pending: bool = False
        self._ready: FillResult | None = None
        self._retired: list[tuple[subprocess.Popen[bytes], Path]] = []
        self._encoding: Iterator[str] | None = None
        self._writing: TextIO | None = None

    @property
    def busy(self) -> bool:
        return self._pending

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    def _clean(self, directory: Path) -> None:
        if directory.resolve().is_relative_to((self.root / "runtime/fill").resolve()):
            shutil.rmtree(directory, ignore_errors=True)

    def _reap(self) -> None:
        remaining: list[tuple[subprocess.Popen[bytes], Path]] = []
        process: subprocess.Popen[bytes]
        directory: Path
        for process, directory in self._retired:
            if process.poll() is None:
                remaining.append((process, directory))
            else:
                self._clean(directory)
        self._retired = remaining

    def _start(self) -> None:
        self._reap()
        if self._process is not None and self._process.poll() is None:
            return
        self.cancel()
        storage: Path = self.root / "runtime/fill"
        storage.mkdir(parents=True, exist_ok=True)
        self._directory = Path(tempfile.mkdtemp(prefix="worker-", dir=storage))
        script: Path = Path(__file__).with_name("fill_worker.py")
        self._process = subprocess.Popen(
            [str(self.python), "-I", "-B", str(script), str(self._directory)],
            cwd=self.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def submit(self, paths: list[list[Vec3]], origin: Vec3, direction: Vec3) -> bool:
        if self._pending:
            return False
        error: OSError | ValueError
        try:
            self._start()
            assert self._directory is not None
            temporary: Path = self._directory / "request.tmp"
            payload: dict[str, object] = {"paths": paths, "origin": origin, "direction": direction}
            encoder: json.JSONEncoder = json.JSONEncoder(separators=(",", ":"), allow_nan=False)
            self._encoding = _chunks(encoder.iterencode(payload))
            self._writing = temporary.open("w", encoding="utf-8")
        except (OSError, ValueError) as error:
            self.cancel()
            self._ready = FillResult(error=f"Could not start fill: {error}")
        self._pending = True
        return True

    def _write(self) -> None:
        if self._encoding is None:
            return
        assert self._writing is not None and self._directory is not None
        deadline: float = time.perf_counter() + 0.004
        while time.perf_counter() < deadline:
            chunk: str | None = next(self._encoding, None)
            if chunk is None:
                self._writing.close()
                self._writing, self._encoding = None, None
                (self._directory / "request.tmp").replace(self._directory / "request.json")
                return
            self._writing.write(chunk)

    def poll(self) -> FillResult | None:
        self._reap()
        if not self._pending:
            return None
        if self._ready is not None:
            result: FillResult = self._ready
            self._ready = None
            self._pending = False
            return result
        assert self._directory is not None and self._process is not None
        code: int | None = self._process.poll()
        if self._encoding is not None and code is not None:
            self.cancel()
            return FillResult(error=f"Fill worker stopped (code {code}); try Fill again")
        error: OSError | ValueError | TypeError
        try:
            self._write()
        except (OSError, ValueError) as error:
            self.cancel()
            return FillResult(error=f"Could not prepare fill: {error}")
        if self._encoding is not None:
            return None
        output: Path = self._directory / "result.json"
        if output.is_file():
            try:
                result = _result(json.loads(output.read_text(encoding="utf-8")))
                output.unlink()
            except (PermissionError, FileNotFoundError):
                return None
            except (OSError, ValueError, TypeError) as error:
                result = FillResult(error=f"Could not read fill: {error}")
                self.cancel()
            self._pending = False
            return result
        code = self._process.poll()
        if code is not None:
            self.cancel()
            return FillResult(error=f"Fill worker stopped (code {code}); try Fill again")
        return None

    def cancel(self) -> None:
        """Terminate work without waiting in Blender's event loop."""
        process: subprocess.Popen[bytes] | None = self._process
        directory: Path | None = self._directory
        self._process, self._directory = None, None
        self._pending, self._ready = False, None
        self._encoding = None
        if self._writing is not None:
            try:
                self._writing.close()
            except OSError:
                pass
            self._writing = None
        if process is not None:
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            if directory is not None:
                self._clean(directory)
                self._retired.append((process, directory))
        elif directory is not None:
            self._clean(directory)
        self._reap()

    def close(self) -> None:
        self.cancel()
