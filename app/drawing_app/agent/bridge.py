"""Local connection between MCP server processes and the running application.

mcp_server.py connects here over TCP on 127.0.0.1 and sends newline-delimited JSON requests, each with
the session token. Client threads only queue requests: pump(), called from a Blender timer, runs them
on the main thread, because Blender's API is not thread-safe. The port and token are written to a
discovery file so agents started outside the app (for example Claude Code in an editor) can connect.
"""

from __future__ import annotations

import io
import json
import os
import queue
import secrets
import socket
import threading
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path

from .transcript import JsonObject, mapping, text

# Read by mcp_server.py; keep the name in sync with its DISCOVERY_FILE.
DISCOVERY_FILE: str = "agent-bridge.json"
# Long enough for screenshots and scripts; the client gives up afterwards.
REQUEST_TIMEOUT: float = 900.0

Handler = Callable[[str, JsonObject], JsonObject]


@dataclass
class Request:
    method: str
    params: JsonObject
    result: Future[JsonObject]


class Bridge:
    """Token-protected localhost server whose requests run on the thread that calls pump()."""

    def __init__(self, handler: Handler, discovery: Path | None = None) -> None:
        self._handler: Handler = handler
        self._discovery: Path | None = discovery
        self.token: str = secrets.token_hex(24)
        self.port: int = 0
        self._server: socket.socket | None = None
        self._requests: queue.Queue[Request] = queue.Queue()
        self._running: bool = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        server: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(8)
        self._server = server
        self.port = int(server.getsockname()[1])
        self._running = True
        threading.Thread(target=self._accept, args=(server,), daemon=True, name="mcp-bridge").start()
        if self._discovery is not None:
            self._discovery.parent.mkdir(parents=True, exist_ok=True)
            payload: JsonObject = {"port": self.port, "token": self.token, "pid": os.getpid()}
            self._discovery.write_text(json.dumps(payload), encoding="utf-8")

    def stop(self) -> None:
        self._running = False
        server: socket.socket | None = self._server
        self._server = None
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        if self._discovery is not None:
            try:
                current: JsonObject = mapping(json.loads(self._discovery.read_text(encoding="utf-8")))
                # Another running instance may have replaced the file.
                if current.get("token") == self.token:
                    self._discovery.unlink()
            except (OSError, ValueError):
                pass
        while True:
            try:
                pending: Request = self._requests.get_nowait()
            except queue.Empty:
                break
            pending.result.set_exception(RuntimeError("Drawing Intelligence is closing"))

    def pump(self, budget: int = 4) -> int:
        """Run queued requests on this thread; returns how many ran."""
        handled: int = 0
        while handled < budget:
            try:
                request: Request = self._requests.get_nowait()
            except queue.Empty:
                break
            handled += 1
            if request.result.cancelled():
                continue
            try:
                request.result.set_result(self._handler(request.method, request.params))
            # Every failure, including bugs in a tool, becomes an error reply.
            except Exception as error:
                request.result.set_exception(error)
        return handled

    def _accept(self, server: socket.socket) -> None:
        while self._running:
            try:
                connection: socket.socket
                connection, _address = server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True, name="mcp-bridge-client").start()

    def _serve(self, connection: socket.socket) -> None:
        stream: io.BufferedRWPair = connection.makefile("rwb")
        try:
            raw: bytes
            for raw in stream:
                try:
                    message: JsonObject = mapping(json.loads(raw.decode("utf-8")))
                except ValueError:
                    break
                if not secrets.compare_digest(text(message.get("token")), self.token):
                    self._send(stream, {"id": message.get("id"), "error": {"message": "Invalid bridge token"}})
                    break
                request: Request = Request(text(message.get("method")), mapping(message.get("params")), Future())
                self._requests.put(request)
                reply: JsonObject
                try:
                    reply = {"id": message.get("id"), "result": request.result.result(timeout=REQUEST_TIMEOUT)}
                except FutureTimeout:
                    request.result.cancel()
                    reply = {"id": message.get("id"), "error": {"message": "Drawing Intelligence did not respond"}}
                # Reported to the MCP client.
                except Exception as error:
                    reply = {"id": message.get("id"), "error": {"message": str(error)}}
                self._send(stream, reply)
        except OSError:
            pass
        finally:
            try:
                stream.close()
                connection.close()
            except OSError:
                pass

    @staticmethod
    def _send(stream: io.BufferedRWPair, message: JsonObject) -> None:
        stream.write((json.dumps(message) + "\n").encode("utf-8"))
        stream.flush()
