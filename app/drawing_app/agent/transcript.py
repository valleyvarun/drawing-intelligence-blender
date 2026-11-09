"""Chat transcript built from Claude Code's stream-json events.

The CLI emits partial `stream_event` deltas while a reply is generated, then one `assistant` event per
completed content block, and `user` events carrying tool results. Streamed text is shown as it arrives
and replaced by the final block text, matched by message id and order.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, TypeAlias

JsonObject: TypeAlias = dict[str, object]
ToolStatus: TypeAlias = Literal["running", "done", "error", "stopped"]
ApprovalStatus: TypeAlias = Literal["pending", "allowed", "denied", "cancelled"]


def mapping(value: object) -> JsonObject:
    """A JSON object, or an empty one for any other value."""
    return value if isinstance(value, dict) else {}


def items(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def text(value: object) -> str:
    return value if isinstance(value, str) else ""


@dataclass
class Entry:
    """Base of every transcript entry; ids are stable for the life of a chat."""

    id: int


@dataclass
class UserMessage(Entry):
    text: str
    # Names of the files and context sent with the message.
    attachments: list[str] = field(default_factory=list)


@dataclass
class AssistantText(Entry):
    text: str
    streaming: bool = False


@dataclass
class ToolCall(Entry):
    tool_id: str
    name: str
    input: JsonObject = field(default_factory=dict)
    status: ToolStatus = "running"
    result: str = ""


@dataclass
class Approval(Entry):
    """A permission prompt from Claude Code, answered from the chat panel."""

    request_id: str
    tool_name: str
    input: JsonObject
    description: str
    status: ApprovalStatus = "pending"


@dataclass
class Notice(Entry):
    text: str
    error: bool = False


def result_text(content: object) -> str:
    """Readable text of a tool result, which is a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    block: object
    for block in items(content):
        data: JsonObject = mapping(block)
        if data.get("type") == "text":
            parts.append(text(data.get("text")))
        elif data.get("type") == "image":
            parts.append("[image]")
    return "\n".join(parts)


class Transcript:
    """Ordered chat entries and the streaming state needed to place incoming events."""

    def __init__(self) -> None:
        self.entries: list[Entry] = []
        # Claude is reasoning before its next visible block.
        self.thinking: bool = False
        self._next_id: int = 1
        self._message: str = ""
        # (message id, block index) -> text entry being streamed.
        self._blocks: dict[tuple[str, int], AssistantText] = {}
        # Message id -> streamed text entries in order, and how many final text blocks have arrived.
        self._streams: dict[str, list[AssistantText]] = {}
        self._finals: dict[str, int] = {}
        self._tools: dict[str, ToolCall] = {}

    def _id(self) -> int:
        value: int = self._next_id
        self._next_id += 1
        return value

    def add_user(self, message: str, attachments: Sequence[str] = ()) -> UserMessage:
        entry: UserMessage = UserMessage(self._id(), message, list(attachments))
        self.entries.append(entry)
        return entry

    def add_notice(self, message: str, error: bool = False) -> Notice:
        entry: Notice = Notice(self._id(), message, error)
        self.entries.append(entry)
        return entry

    def add_approval(self, request_id: str, tool_name: str, tool_input: JsonObject, description: str) -> Approval:
        entry: Approval = Approval(self._id(), request_id, tool_name, tool_input, description)
        self.entries.append(entry)
        return entry

    def approval(self, request_id: str) -> Approval | None:
        entry: Entry
        for entry in reversed(self.entries):
            if isinstance(entry, Approval) and entry.request_id == request_id:
                return entry
        return None

    def pending(self) -> list[Approval]:
        result: list[Approval] = []
        entry: Entry
        for entry in self.entries:
            if isinstance(entry, Approval) and entry.status == "pending":
                result.append(entry)
        return result

    def entry(self, entry_id: int) -> Entry | None:
        entry: Entry
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        return None

    def apply(self, event: JsonObject) -> None:
        """Place one stream-json event; unknown event types are ignored."""
        # Subagent internals stay inside their Task tool call.
        if event.get("parent_tool_use_id"):
            return
        kind: object = event.get("type")
        if kind == "stream_event":
            self._stream(mapping(event.get("event")), text(event.get("api_message_id")))
        elif kind == "assistant":
            self._assistant(mapping(event.get("message")))
        elif kind == "user":
            self._results(mapping(event.get("message")))

    def _stream(self, event: JsonObject, message_id: str) -> None:
        kind: object = event.get("type")
        if kind == "message_start":
            self._message = text(mapping(event.get("message")).get("id"))
            return
        identifier: str = message_id or self._message
        index: object = event.get("index")
        position: int = index if isinstance(index, int) else -1
        if kind == "content_block_start":
            block: JsonObject = mapping(event.get("content_block"))
            block_type: object = block.get("type")
            if block_type == "text":
                entry: AssistantText = AssistantText(self._id(), text(block.get("text")), streaming=True)
                self.entries.append(entry)
                self._blocks[(identifier, position)] = entry
                self._streams.setdefault(identifier, []).append(entry)
                self.thinking = False
            elif block_type == "thinking":
                self.thinking = True
            elif block_type == "tool_use":
                self._tool(text(block.get("id")), text(block.get("name")))
                self.thinking = False
        elif kind == "content_block_delta":
            delta: JsonObject = mapping(event.get("delta"))
            if delta.get("type") == "text_delta":
                streamed: AssistantText | None = self._blocks.get((identifier, position))
                if streamed is not None:
                    streamed.text += text(delta.get("text"))
            elif delta.get("type") == "thinking_delta":
                self.thinking = True
        elif kind == "content_block_stop":
            finished: AssistantText | None = self._blocks.pop((identifier, position), None)
            if finished is not None:
                finished.streaming = False
        elif kind == "message_stop":
            self.thinking = False

    def _tool(self, tool_id: str, name: str) -> ToolCall:
        call: ToolCall | None = self._tools.get(tool_id)
        if call is None:
            call = ToolCall(self._id(), tool_id, name)
            self.entries.append(call)
            if tool_id:
                self._tools[tool_id] = call
        return call

    def _assistant(self, message: JsonObject) -> None:
        identifier: str = text(message.get("id"))
        block: object
        for block in items(message.get("content")):
            data: JsonObject = mapping(block)
            block_type: object = data.get("type")
            if block_type == "text":
                value: str = text(data.get("text"))
                streamed: list[AssistantText] = self._streams.setdefault(identifier, [])
                count: int = self._finals.get(identifier, 0)
                self._finals[identifier] = count + 1
                if count < len(streamed):
                    streamed[count].text = value
                    streamed[count].streaming = False
                elif value.strip():
                    entry: AssistantText = AssistantText(self._id(), value)
                    self.entries.append(entry)
                    streamed.append(entry)
            elif block_type == "tool_use":
                call: ToolCall = self._tool(text(data.get("id")), text(data.get("name")))
                call.name = text(data.get("name")) or call.name
                call.input = mapping(data.get("input"))
        self.thinking = False

    def _results(self, message: JsonObject) -> None:
        block: object
        for block in items(message.get("content")):
            data: JsonObject = mapping(block)
            if data.get("type") != "tool_result":
                continue
            call: ToolCall | None = self._tools.get(text(data.get("tool_use_id")))
            if call is not None:
                call.status = "error" if data.get("is_error") is True else "done"
                call.result = result_text(data.get("content"))

    def finish_turn(self) -> None:
        """A turn ended: nothing streams any more, and unanswered prompts no longer apply."""
        self.thinking = False
        entry: Entry
        for entry in self.entries:
            if isinstance(entry, AssistantText):
                entry.streaming = False
            elif isinstance(entry, ToolCall) and entry.status == "running":
                entry.status = "stopped"
            elif isinstance(entry, Approval) and entry.status == "pending":
                entry.status = "cancelled"
        self._blocks.clear()


def tool_label(name: str) -> tuple[str, str]:
    """Display name and MCP server of a tool, e.g. ("draw_stroke", "blender")."""
    if name.startswith("mcp__"):
        parts: list[str] = name.split("__", 2)
        if len(parts) == 3:
            return parts[2], parts[1]
    return name, ""


def tool_summary(name: str, tool_input: JsonObject, limit: int = 80) -> str:
    """One line describing what a tool call does, from its most telling input."""
    label: str = tool_label(name)[0]
    value: str = ""
    key: str
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description", "name", "prompt"):
        if isinstance(tool_input.get(key), str):
            value = text(tool_input.get(key))
            break
    if not value and label == "execute_python":
        value = text(tool_input.get("code")).strip().split("\n", 1)[0]
    if not value and label == "draw_stroke":
        value = f"{text(tool_input.get('kind')) or 'pipe'} · {len(items(tool_input.get('points')))} points"
    if not value and tool_input:
        value = json.dumps(tool_input, ensure_ascii=False)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"
