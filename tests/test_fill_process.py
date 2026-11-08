"""Isolated fill worker responsiveness, caching, failures, and cleanup."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from drawing_app.tools.fill_process import FillProcess, FillResult, Vec3

ROOT: Path = Path(__file__).resolve().parents[1]
POINTS: list[list[Vec3]] = [[(0.0, 0.0, 5.0), (2.0, 0.0, 5.0), (2.0, 2.0, 5.0), (0.0, 2.0, 5.0), (0.0, 0.0, 5.0)]]


def module() -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(
        "_fill_process_test", ROOT / "app/drawing_app/tools/fill_process.py"
    )
    assert spec is not None and spec.loader is not None
    result: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {spec.name: result}):
        spec.loader.exec_module(result)
    return result


class FillProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport: ModuleType = module()
        self.process: FillProcess = self.transport.FillProcess(ROOT)

    def tearDown(self) -> None:
        self.process.close()
        deadline: float = time.monotonic() + 2.0
        while self.process._retired and time.monotonic() < deadline:
            time.sleep(0.01)
            self.process.poll()
        self.assertFalse(self.process._retired)

    def result(self) -> FillResult:
        deadline: float = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            result: FillResult | None = self.process.poll()
            if result is not None:
                return result
            time.sleep(0.01)
        self.fail("The isolated fill worker did not return")

    def test_real_process_returns_plane_mesh_and_reuses_curve_cache(self) -> None:
        started: float = time.monotonic()
        self.assertTrue(self.process.submit(POINTS, (1.0, 1.0, 9.0), (0.0, 0.0, -1.0)))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(self.process.busy)
        self.assertFalse(self.process.submit(POINTS, (1.0, 1.0, 9.0), (0.0, 0.0, -1.0)))
        directory: Path | None = self.process._directory
        assert directory is not None
        self.assertTrue(directory.is_relative_to(ROOT / "runtime/fill"))
        pid: int | None = self.process.pid
        self.assertIsNotNone(pid)
        first: FillResult = self.result()
        self.assertFalse(first.error, first.error)
        self.assertTrue(first.faces)
        self.assertFalse(first.cached)
        point: Vec3
        for point in first.points:
            self.assertAlmostEqual(point[2], 5.0)
        self.assertFalse(self.process.busy)
        self.assertTrue(self.process.submit(POINTS, (0.5, 0.5, 9.0), (0.0, 0.0, -1.0)))
        second: FillResult = self.result()
        self.assertFalse(second.error, second.error)
        self.assertTrue(second.cached)
        self.assertEqual(pid, self.process.pid)
        self.assertTrue(self.process.submit(POINTS, (4.0, 4.0, 9.0), (0.0, 0.0, -1.0)))
        outside: FillResult = self.result()
        self.assertTrue(outside.cached)
        self.assertFalse(outside.faces)
        self.assertFalse(outside.error, outside.error)
        moved: list[list[Vec3]] = [[]]
        for point in POINTS[0]:
            moved[0].append((point[0], point[1], point[2] + 1.0))
        self.assertTrue(self.process.submit(moved, (1.0, 1.0, 9.0), (0.0, 0.0, -1.0)))
        changed: FillResult = self.result()
        self.assertFalse(changed.error, changed.error)
        self.assertFalse(changed.cached)
        for point in changed.points:
            self.assertAlmostEqual(point[2], 6.0)

    def test_cancel_does_not_wait_and_next_request_recovers(self) -> None:
        self.assertTrue(self.process.submit(POINTS, (1.0, 1.0, 9.0), (0.0, 0.0, -1.0)))
        pid: int | None = self.process.pid
        started: float = time.monotonic()
        self.process.cancel()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertFalse(self.process.busy)
        self.assertIsNone(self.process.poll())
        self.assertTrue(self.process.submit(POINTS, (1.0, 1.0, 9.0), (0.0, 0.0, -1.0)))
        self.assertNotEqual(self.process.pid, pid)
        result: FillResult = self.result()
        self.assertFalse(result.error, result.error)
        self.assertTrue(result.faces)

    def test_large_snapshot_encoding_yields_between_polls(self) -> None:
        paths: list[list[Vec3]] = [[]]
        index: int
        for index in range(100000):
            paths[0].append((float(index), 0.0, 5.0))
        encoder: MagicMock
        with patch.object(
            self.transport.json.JSONEncoder, "iterencode", wraps=self.transport.json.JSONEncoder().iterencode
        ) as encoder:
            started: float = time.monotonic()
            self.assertTrue(self.process.submit(paths, (1.0, 1.0, 9.0), (0.0, 0.0, -1.0)))
            self.assertLess(time.monotonic() - started, 0.5)
            encoder.assert_called_once()
        directory: Path | None = self.process._directory
        assert directory is not None
        self.assertEqual((directory / "request.tmp").stat().st_size, 0)
        self.assertIsNone(self.process.poll())
        self.assertIsNotNone(self.process._encoding)
        self.assertFalse((directory / "request.json").exists())
        self.process.cancel()
        self.assertIsNone(self.process._writing)

    def test_worker_exit_becomes_error_and_does_not_stick_busy(self) -> None:
        self.assertTrue(self.process.submit(POINTS, (1.0, 1.0, 9.0), (0.0, 0.0, -1.0)))
        assert self.process._process is not None
        self.process._process.kill()
        result: FillResult = self.result()
        self.assertIn("stopped", result.error)
        self.assertFalse(self.process.busy)
        self.assertTrue(self.process.submit(POINTS, (1.0, 1.0, 9.0), (0.0, 0.0, -1.0)))
        self.assertFalse(self.result().error)

    def test_spawn_failure_is_reported_without_raising_in_input_handler(self) -> None:
        with patch.object(self.transport.subprocess, "Popen", side_effect=OSError("unavailable")):
            self.assertTrue(self.process.submit(POINTS, (1.0, 1.0, 9.0), (0.0, 0.0, -1.0)))
        result: FillResult = self.result()
        self.assertIn("unavailable", result.error)
        self.assertFalse(self.process.busy)

    def test_transient_result_lock_is_retried_without_losing_the_fill(self) -> None:
        process: MagicMock = MagicMock()
        process.poll.return_value = None
        with patch.object(self.transport.subprocess, "Popen", return_value=process):
            self.assertTrue(self.process.submit(POINTS, (1.0, 1.0, 9.0), (0.0, 0.0, -1.0)))
            self.assertIsNone(self.process.poll())
            directory: Path | None = self.process._directory
            assert directory is not None
            output: Path = directory / "result.json"
            output.write_text(json.dumps({"points": POINTS[0][:3], "faces": [[0, 1, 2]]}), encoding="utf-8")
            with patch.object(Path, "read_text", side_effect=PermissionError("sharing violation")):
                self.assertIsNone(self.process.poll())
                self.assertTrue(self.process.busy)
            with patch.object(Path, "unlink", side_effect=PermissionError("sharing violation")):
                self.assertIsNone(self.process.poll())
                self.assertTrue(self.process.busy)
            result: FillResult | None = self.process.poll()
            assert result is not None
            self.assertFalse(result.error)
            self.assertEqual(result.faces, [(0, 1, 2)])
            self.assertFalse(self.process.busy)
            process.poll.return_value = 0

    def test_poll_never_waits_for_a_running_worker(self) -> None:
        process: MagicMock = MagicMock()
        process.poll.return_value = None
        with patch.object(self.transport.subprocess, "Popen", return_value=process):
            self.assertTrue(self.process.submit(POINTS, (1.0, 1.0, 9.0), (0.0, 0.0, -1.0)))
            self.assertIsNone(self.process.poll())
            process.wait.assert_not_called()
            process.communicate.assert_not_called()
            self.process.cancel()
            process.kill.assert_called_once()
            process.wait.assert_not_called()
            process.poll.return_value = 0


if __name__ == "__main__":
    unittest.main()
