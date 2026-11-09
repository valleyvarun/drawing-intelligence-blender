"""Blender MCP server for Drawing Intelligence: Model Context Protocol over stdio (JSON-RPC 2.0).

An MCP client such as Claude Code starts this script as its own process, normally with the engine's
bundled Python. It uses only the standard library and never imports Blender or the application
package: tool listings and calls are relayed to the running application's bridge (agent/bridge.py),
which runs them on Blender's main thread. The tools are the scripting API in `drawing_app.api`.

The bridge address comes from DRAWING_BRIDGE_PORT and DRAWING_BRIDGE_TOKEN when the app starts the
client itself, or else from the discovery file the app writes to its configuration folder.
"""

from __future__ import annotations

import io
import json
import os
import socket
import sys
from pathlib import Path
from typing import BinaryIO, TypeAlias

JsonObject: TypeAlias = dict[str, object]

SERVER_NAME: str = "drawing-intelligence"
SERVER_VERSION: str = "1.0.0"
# Used when a client does not request a protocol version; tools work alike in every revision.
PROTOCOL_VERSION: str = "2025-06-18"
# Written by agent/bridge.py; keep the name in sync with its DISCOVERY_FILE.
DISCOVERY_FILE: str = "agent-bridge.json"
TIMEOUT: float = 900.0
INSTRUCTIONS: str = (
    "These tools control Drawing Intelligence, a 3D sketching app built on Blender. Lengths are metres "
    "and Z is up. Start with scene_info, check visual results with viewport_screenshot, and use "
    "execute_python (full bpy access, plus `api` for these same tools) for anything else."
)


class BridgeUnavailable(Exception):
    """The application is not running, or its bridge refused the connection."""


def config_directory() -> Path:
    """The app's configuration folder, resolved like core/state.py does."""
    root: Path = Path(__file__).resolve().parents[3]
    return (root / os.environ.get("DRAWING_APP_CONFIG", "runtime/config")).resolve()


def bridge_address() -> tuple[int, str]:
    port: str = os.environ.get("DRAWING_BRIDGE_PORT", "")
    token: str = os.environ.get("DRAWING_BRIDGE_TOKEN", "")
    if port.isdigit() and token:
        return int(port), token
    try:
        decoded: object = json.loads((config_directory() / DISCOVERY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise BridgeUnavailable("Drawing Intelligence is not running. Start the app, then try again.") from error
    data: JsonObject = decoded if isinstance(decoded, dict) else {}
    found_port: object = data.get("port")
    found_token: object = data.get("token")
    if not isinstance(found_port, int) or not isinstance(found_token, str):
        raise BridgeUnavailable("The Drawing Intelligence bridge file is invalid. Restart the app.")
    return found_port, found_token


class BridgeClient:
    """One persistent connection to the app's bridge, reopened once if it drops."""

    def __init__(self) -> None:
        self._socket: socket.socket | None = None
        self._stream: io.BufferedRWPair | None = None
        self._token: str = ""
        self._next: int = 0

    def _connect(self) -> io.BufferedRWPair:
        port: int
        port, self._token = bridge_address()
        try:
            connection: socket.socket = socket.create_connection(("127.0.0.1", port), timeout=10.0)
        except OSError as error:
            raise BridgeUnavailable("Drawing Intelligence is not running. Start the app, then try again.") from error
        connection.settimeout(TIMEOUT)
        self._socket = connection
        self._stream = connection.makefile("rwb")
        return self._stream

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except OSError:
                pass
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None
        self._stream = None

    def request(self, method: str, params: JsonObject) -> JsonObject:
        attempt: int
        for attempt in range(2):
            stream: io.BufferedRWPair = self._stream if self._stream is not None else self._connect()
            self._next += 1
            message: JsonObject = {"token": self._token, "id": self._next, "method": method, "params": params}
            try:
                stream.write((json.dumps(message) + "\n").encode("utf-8"))
                stream.flush()
                line: bytes = stream.readline()
            except OSError:
                line = b""
            if not line:
                # The app restarted or closed the connection: reconnect once.
                self.close()
                if attempt == 0:
                    continue
                raise BridgeUnavailable("Lost the connection to Drawing Intelligence.")
            reply: object = json.loads(line.decode("utf-8"))
            data: JsonObject = reply if isinstance(reply, dict) else {}
            error: object = data.get("error")
            if isinstance(error, dict):
                raise BridgeUnavailable(str(error.get("message", "The app reported an error.")))
            result: object = data.get("result")
            return result if isinstance(result, dict) else {}
        raise BridgeUnavailable("Lost the connection to Drawing Intelligence.")


def success(identifier: object, result: JsonObject) -> JsonObject:
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def failure(identifier: object, code: int, message: str) -> JsonObject:
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


def handle(client: BridgeClient, message: JsonObject) -> JsonObject | None:
    """Answer one JSON-RPC message; notifications get no reply."""
    method: object = message.get("method")
    identifier: object = message.get("id")
    raw_params: object = message.get("params")
    params: JsonObject = raw_params if isinstance(raw_params, dict) else {}
    if "id" not in message:
        return None
    if method == "initialize":
        requested: object = params.get("protocolVersion")
        return success(
            identifier,
            {
                "protocolVersion": requested if isinstance(requested, str) else PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return success(identifier, {})
    if method == "tools/list":
        try:
            return success(identifier, {"tools": client.request("list_tools", {}).get("tools", [])})
        except BridgeUnavailable as error:
            return failure(identifier, -32000, str(error))
    if method == "tools/call":
        name: object = params.get("name")
        arguments: object = params.get("arguments")
        try:
            result: JsonObject = client.request(
                "call_tool", {"name": name, "arguments": arguments if isinstance(arguments, dict) else {}}
            )
        except BridgeUnavailable as error:
            result = {"content": [{"type": "text", "text": str(error)}], "isError": True}
        return success(identifier, result)
    return failure(identifier, -32601, f"Method not found: {method}")


def main() -> int:
    client: BridgeClient = BridgeClient()
    output: BinaryIO = sys.stdout.buffer
    raw: bytes
    for raw in sys.stdin.buffer:
        line: str = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            decoded: object = json.loads(line)
        except ValueError:
            reply: JsonObject | None = failure(None, -32700, "Parse error")
        else:
            reply = handle(client, decoded) if isinstance(decoded, dict) else failure(None, -32600, "Invalid request")
        if reply is not None:
            output.write((json.dumps(reply) + "\n").encode("utf-8"))
            output.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
