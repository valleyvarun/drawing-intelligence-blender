"""Blender MCP server process and the application's bridge, exercised without Blender."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import IO, TYPE_CHECKING, cast
from unittest.mock import patch

if TYPE_CHECKING:
    from drawing_app.agent.bridge import Bridge

ROOT: Path = Path(__file__).resolve().parents[1]
SERVER: Path = ROOT / "app/drawing_app/agent/mcp_server.py"
JsonObject = dict[str, object]


def module(name: str, path: str) -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    result: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: result}):
        spec.loader.exec_module(result)
    return result


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        package: ModuleType = ModuleType("_mcp")
        setattr(package, "__path__", [])
        transcript: ModuleType = module("_mcp.transcript", "app/drawing_app/agent/transcript.py")
        with patch.dict(sys.modules, {"_mcp": package, "_mcp.transcript": transcript}):
            self.bridge_module: ModuleType = module("_mcp.bridge", "app/drawing_app/agent/bridge.py")
        self.folder: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.config: Path = Path(self.folder.name) / "config"
        self.calls: list[tuple[str, JsonObject]] = []
        self.bridge: Bridge = cast(
            "Bridge", self.bridge_module.Bridge(self.handle, self.config / self.bridge_module.DISCOVERY_FILE)
        )
        self.bridge.start()
        self.pumping: bool = True
        self.pump: threading.Thread = threading.Thread(target=self.run_pump, daemon=True)
        self.pump.start()
        self.processes: list[subprocess.Popen[bytes]] = []

    def tearDown(self) -> None:
        process: subprocess.Popen[bytes]
        for process in self.processes:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=10)
            stream: IO[bytes] | None
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        self.pumping = False
        self.pump.join(timeout=5)
        self.bridge.stop()
        self.folder.cleanup()

    def run_pump(self) -> None:
        # Blender's timer pumps the bridge on its main thread; a thread stands in for it here.
        while self.pumping:
            self.bridge.pump()
            time.sleep(0.005)

    def handle(self, method: str, params: JsonObject) -> JsonObject:
        self.calls.append((method, params))
        if method == "list_tools":
            return {"tools": [{"name": "scene_info", "description": "Summary", "inputSchema": {"type": "object"}}]}
        if params.get("name") == "broken":
            raise RuntimeError("tool runner failed")
        return {"content": [{"type": "text", "text": f"called {params.get('name')}"}], "isError": False}

    def server(self, environment: dict[str, str]) -> subprocess.Popen[bytes]:
        env: dict[str, str] = dict(os.environ)
        name: str
        for name in ("DRAWING_BRIDGE_PORT", "DRAWING_BRIDGE_TOKEN", "DRAWING_APP_CONFIG"):
            env.pop(name, None)
        env.update(environment)
        process: subprocess.Popen[bytes] = subprocess.Popen(
            [sys.executable, "-I", str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.processes.append(process)
        return process

    def ask(self, process: subprocess.Popen[bytes], message: JsonObject | str) -> JsonObject:
        assert process.stdin is not None and process.stdout is not None
        line: str = message if isinstance(message, str) else json.dumps(message)
        process.stdin.write((line + "\n").encode("utf-8"))
        process.stdin.flush()
        reply: object = json.loads(process.stdout.readline().decode("utf-8"))
        assert isinstance(reply, dict)
        return cast(JsonObject, reply)

    def direct(self) -> subprocess.Popen[bytes]:
        return self.server({"DRAWING_BRIDGE_PORT": str(self.bridge.port), "DRAWING_BRIDGE_TOKEN": self.bridge.token})

    def test_protocol_relays_listing_and_calls(self) -> None:
        process: subprocess.Popen[bytes] = self.direct()
        initialized: JsonObject = self.ask(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test"}},
            },
        )
        result: JsonObject = cast(JsonObject, initialized["result"])
        self.assertEqual(result["protocolVersion"], "2025-11-25")
        self.assertEqual(cast(JsonObject, result["serverInfo"])["name"], "drawing-intelligence")
        self.assertEqual(result["capabilities"], {"tools": {"listChanged": False}})
        assert process.stdin is not None
        # Notifications get no reply, so the next answer belongs to the next request.
        process.stdin.write(b'{"jsonrpc": "2.0", "method": "notifications/initialized"}\n')
        self.assertEqual(self.ask(process, {"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"], {})
        listed: JsonObject = self.ask(process, {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        self.assertEqual(cast(list[JsonObject], cast(JsonObject, listed["result"])["tools"])[0]["name"], "scene_info")
        called: JsonObject = self.ask(
            process,
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "draw", "arguments": {"a": 1}}},
        )
        self.assertEqual(called["result"], {"content": [{"type": "text", "text": "called draw"}], "isError": False})
        self.assertIn(("call_tool", {"name": "draw", "arguments": {"a": 1}}), self.calls)
        failed: JsonObject = self.ask(
            process, {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "broken"}}
        )
        failure: JsonObject = cast(JsonObject, failed["result"])
        self.assertTrue(failure["isError"])
        self.assertIn("tool runner failed", json.dumps(failure["content"]))
        missing: JsonObject = self.ask(process, {"jsonrpc": "2.0", "id": 6, "method": "resources/list"})
        self.assertEqual(cast(JsonObject, missing["error"])["code"], -32601)
        self.assertEqual(cast(JsonObject, self.ask(process, "{not json")["error"])["code"], -32700)

    def test_discovery_file_token_checks_and_absent_app(self) -> None:
        discovered: subprocess.Popen[bytes] = self.server({"DRAWING_APP_CONFIG": str(self.config)})
        listed: JsonObject = self.ask(discovered, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertIn("tools", cast(JsonObject, listed["result"]))
        forged: subprocess.Popen[bytes] = self.server(
            {"DRAWING_BRIDGE_PORT": str(self.bridge.port), "DRAWING_BRIDGE_TOKEN": "wrong"}
        )
        refused: JsonObject = self.ask(forged, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertIn("Invalid bridge token", str(cast(JsonObject, refused["error"])["message"]))
        absent: subprocess.Popen[bytes] = self.server({"DRAWING_APP_CONFIG": str(Path(self.folder.name) / "none")})
        unavailable: JsonObject = self.ask(absent, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(cast(JsonObject, unavailable["error"])["code"], -32000)
        self.assertIn("not running", str(cast(JsonObject, unavailable["error"])["message"]))
        call: JsonObject = self.ask(
            absent, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "scene_info"}}
        )
        self.assertTrue(cast(JsonObject, call["result"])["isError"])

    def test_stopping_removes_only_its_own_discovery_file(self) -> None:
        discovery: Path = self.config / self.bridge_module.DISCOVERY_FILE
        data: object = json.loads(discovery.read_text(encoding="utf-8"))
        self.assertEqual(cast(JsonObject, data)["port"], self.bridge.port)
        replacement: str = json.dumps({"port": 1, "token": "another instance"})
        discovery.write_text(replacement, encoding="utf-8")
        self.bridge.stop()
        self.assertEqual(discovery.read_text(encoding="utf-8"), replacement)
        restarted: Bridge = cast("Bridge", self.bridge_module.Bridge(self.handle, discovery))
        restarted.start()
        restarted.stop()
        self.assertFalse(discovery.exists())


if __name__ == "__main__":
    unittest.main()
