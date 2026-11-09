"""Claude Code chat panel, drawn below the layers list in the right-hand panel.

The panel only draws and records widgets; overlay.py routes clicks, typing, and scrolling, and the
conversation itself lives in agent/session.py.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field

from .. import agent
from ..agent.session import MODES, Account, ChatSession, ModelChoice, Status
from ..agent.transcript import (
    Approval,
    AssistantText,
    Entry,
    JsonObject,
    Notice,
    ToolCall,
    UserMessage,
    tool_label,
    tool_summary,
)
from ..core.state import AppState, get_state
from . import paint
from .widgets import Edit, MenuItem, Rect, Widget, hit

SIZE: float = 12.0
LINE: float = 16.0
GAP: float = 10.0
PAD: float = 8.0
HEADER: float = 30.0
INPUT_LINES: int = 6
DETAIL_LINES: int = 14
TOOLBAR: float = 22.0

BACKGROUND: paint.Color = (0.13, 0.14, 0.145, 1.0)
BUBBLE: paint.Color = (0.21, 0.22, 0.24, 1.0)
CODE: paint.Color = (0.085, 0.09, 0.095, 1.0)
FIELD: paint.Color = (0.10, 0.105, 0.11, 1.0)
# Claude's brand orange marks the panel title.
ACCENT: paint.Color = (0.85, 0.47, 0.34, 1.0)
BLUE: paint.Color = (0.45, 0.65, 0.95, 1.0)
GREEN: paint.Color = (0.45, 0.76, 0.46, 1.0)
RED: paint.Color = (0.92, 0.42, 0.36, 1.0)
AMBER: paint.Color = (0.92, 0.72, 0.32, 1.0)

EXAMPLES: tuple[str, ...] = (
    "Draw a spiral staircase out of pipes",
    "Describe what is in this drawing",
    "Make every stroke in the active group blue",
)
EFFORTS: dict[str, str] = {"low": "Low", "medium": "Medium", "high": "High", "xhigh": "Extra high", "max": "Max"}
# Model aliases offered before Claude Code has sent its own list.
FALLBACK_MODELS: tuple[ModelChoice, ...] = (
    ModelChoice("default", "Default (recommended)", "Claude Code's default model", tuple(EFFORTS)),
    ModelChoice("opus", "Opus", "", tuple(EFFORTS)),
    ModelChoice("sonnet", "Sonnet", "", tuple(EFFORTS)),
    ModelChoice("haiku", "Haiku", "", tuple(EFFORTS)),
)
STATUS: dict[Status, tuple[str, paint.Color]] = {
    "idle": ("Ready", paint.MUTED),
    "starting": ("Starting…", BLUE),
    "working": ("Working…", BLUE),
    "waiting": ("Needs approval", AMBER),
    "stopping": ("Stopping…", AMBER),
}


@dataclass
class Piece:
    """Text, a box, or a button, placed relative to an entry's top-left corner (y grows downward)."""

    kind: str
    x: float
    y: float
    w: float = 0.0
    h: float = LINE
    text: str = ""
    color: paint.Color = paint.TEXT
    action: str = ""
    payload: object = None
    tooltip: str = ""


@dataclass
class Layout:
    height: float
    pieces: list[Piece] = field(default_factory=list)


def _blocks(source: str) -> list[tuple[bool, str]]:
    """Split Markdown into prose and fenced code blocks, lightly cleaned for display."""
    blocks: list[tuple[bool, str]] = []
    code: bool = False
    current: list[str] = []
    line: str
    for line in source.split("\n"):
        if line.lstrip().startswith("```"):
            if current:
                blocks.append((code, "\n".join(current)))
            current = []
            code = not code
            continue
        if not code:
            stripped: str = line.lstrip()
            if stripped.startswith("#"):
                line = stripped.lstrip("#").strip()
            elif stripped.startswith(("- ", "* ")):
                line = line[: len(line) - len(stripped)] + "• " + stripped[2:]
            line = line.replace("**", "")
        current.append(line)
    if current:
        blocks.append((code, "\n".join(current)))
    return blocks


def _status_line(entry: ToolCall) -> tuple[paint.Color, str]:
    if entry.status == "running":
        pulse: float = 0.55 + 0.45 * math.sin(time.monotonic() * 6.0)
        return (BLUE[0], BLUE[1], BLUE[2], pulse), ""
    if entry.status == "done":
        return GREEN, ""
    if entry.status == "error":
        return RED, " (failed)"
    return paint.MUTED, " (stopped)"


def _detail(entry: Approval) -> str:
    """What an approval would do: the command, code, file, or input."""
    data: JsonObject = entry.input
    label: str = tool_label(entry.tool_name)[0]
    value: object
    if label == "execute_python":
        value = data.get("code")
    elif label == "ExitPlanMode":
        value = data.get("plan")
    elif label in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        value = data.get("file_path") or data.get("notebook_path")
    else:
        value = data.get("command")
    if isinstance(value, str) and value.strip():
        return value
    return json.dumps(data, indent=1, ensure_ascii=False)


class ChatPanel:
    """Drawing state for the chat: draft text, scrolling, expanded tool calls, and cached wrapping."""

    def __init__(self) -> None:
        self.draft: str = ""
        # Distance scrolled up from the newest message; 0 follows new content.
        self.offset: float = 0.0
        self.limit: float = 0.0
        # Transcript area, for routing the mouse wheel.
        self.bounds: Rect = (0.0, 0.0, 0.0, 0.0)
        self.expanded: set[int] = set()
        self._content: float = 0.0
        self._transcript: int = 0
        self._wraps: dict[tuple[str, float], list[tuple[int, int]]] = {}

    def scroll(self, amount: float) -> None:
        self.offset = max(0.0, min(self.limit, self.offset + amount))

    def toggle(self, entry_id: int) -> None:
        self.expanded.symmetric_difference_update({entry_id})

    def take_draft(self) -> str:
        value: str = self.draft
        self.draft = ""
        self.offset = 0.0
        return value

    def _wrap(self, value: str, width: float) -> list[str]:
        key: tuple[str, float] = (value, round(width))
        spans: list[tuple[int, int]] | None = self._wraps.get(key)
        if spans is None:
            if len(self._wraps) > 2000:
                self._wraps.clear()
            spans = paint.wrap(value, width, SIZE)
            self._wraps[key] = spans
        lines: list[str] = []
        start: int
        end: int
        for start, end in spans:
            lines.append(value[start:end].rstrip())
        return lines

    def _text(self, layout: Layout, value: str, x: float, width: float, color: paint.Color) -> None:
        line: str
        for line in self._wrap(value, width):
            layout.pieces.append(Piece("text", x, layout.height, text=line, color=color))
            layout.height += LINE

    def _code(self, layout: Layout, value: str, width: float, limit: int = 0) -> None:
        lines: list[str] = self._wrap(value, width - 12)
        if limit and len(lines) > limit:
            lines = lines[:limit] + [f"… {len(lines) - limit} more lines"]
        top: float = layout.height
        layout.pieces.append(Piece("box", 0, top, width, len(lines) * LINE + 8, color=CODE))
        line: str
        for line in lines:
            layout.pieces.append(Piece("text", 6, layout.height + 4, text=line, color=(0.74, 0.78, 0.82, 1.0)))
            layout.height += LINE
        layout.height += 8

    def _layout(self, entry: Entry, width: float, session: ChatSession) -> Layout:
        layout: Layout = Layout(0.0)
        if isinstance(entry, UserMessage):
            lines: list[str] = self._wrap(entry.text, width - 16)
            layout.pieces.append(Piece("box", 0, 0, width, len(lines) * LINE + 12, color=BUBBLE))
            layout.height = 6
            self._text(layout, entry.text, 8, width - 16, paint.TEXT)
            layout.height += 6
        elif isinstance(entry, AssistantText):
            code: bool
            value: str
            for code, value in _blocks(entry.text):
                if code:
                    self._code(layout, value, width)
                elif value.strip():
                    self._text(layout, value.strip("\n"), 0, width, paint.TEXT)
            layout.height = max(layout.height, LINE)
        elif isinstance(entry, ToolCall):
            self._tool(layout, entry, width)
        elif isinstance(entry, Approval):
            self._approval(layout, entry, width)
        elif isinstance(entry, Notice):
            self._text(layout, entry.text, 0, width, RED if entry.error else paint.MUTED)
            if entry.error and session.missing_cli and entry is session.transcript.entries[-1]:
                layout.pieces.append(
                    Piece(
                        "button",
                        0,
                        layout.height + 4,
                        150,
                        24,
                        "Locate Claude Code…",
                        action="agent",
                        payload=("locate",),
                    )
                )
                layout.height += 30
        return layout

    def _tool(self, layout: Layout, entry: ToolCall, width: float) -> None:
        name: str
        server: str
        name, server = tool_label(entry.name)
        color: paint.Color
        suffix: str
        color, suffix = _status_line(entry)
        title: str = ("Blender · " if server == "blender" else "") + name + suffix
        summary: str = tool_summary(entry.name, entry.input)
        layout.pieces.append(Piece("dot", 5, 0, color=color))
        layout.pieces.append(Piece("text", 16, 0, text=title, color=paint.TEXT))
        title_width: float = paint.measure(title, SIZE) + 24
        if summary and title_width < width - 30:
            layout.pieces.append(Piece("text", title_width, 0, w=width - title_width, text=summary, color=paint.MUTED))
        layout.pieces.append(
            Piece("hit", 0, 0, width, LINE, action="chat", payload=("toggle", entry.id), tooltip="Show details")
        )
        layout.height = LINE + 2
        if entry.id in self.expanded:
            if entry.input:
                self._code(layout, json.dumps(entry.input, indent=1, ensure_ascii=False), width, DETAIL_LINES)
            if entry.result:
                layout.height += 2
                self._code(layout, entry.result, width, DETAIL_LINES)

    def _approval(self, layout: Layout, entry: Approval, width: float) -> None:
        name: str = tool_label(entry.tool_name)[0]
        if entry.status != "pending":
            words: dict[str, str] = {"allowed": "Allowed", "denied": "Denied", "cancelled": "Skipped"}
            self._text(layout, f"{words.get(entry.status, 'Answered')} {name}", 0, width, paint.MUTED)
            return
        top: float = layout.height
        layout.height += 8
        title: str = "Approve this plan?" if name == "ExitPlanMode" else f"Allow {name}?"
        self._text(layout, title, 10, width - 20, AMBER)
        if entry.description and entry.description != name:
            self._text(layout, entry.description, 10, width - 20, paint.MUTED)
        layout.height += 4
        detail: Layout = Layout(0.0)
        self._code(detail, _detail(entry), width - 20, 8)
        piece: Piece
        for piece in detail.pieces:
            piece.x += 10
            piece.y += layout.height
            layout.pieces.append(piece)
        layout.height += detail.height + 2
        buttons: tuple[tuple[str, str, str], ...] = (
            ("Allow", "allow", "Run it once"),
            ("Always", "always", f"Allow {name} for the rest of this chat"),
            ("Deny", "deny", "Do not run it"),
        )
        each: float = (width - 20 - 12) / 3
        index: int
        label: str
        decision: str
        tip: str
        for index, (label, decision, tip) in enumerate(buttons):
            layout.pieces.append(
                Piece(
                    "button",
                    10 + index * (each + 6),
                    layout.height,
                    each,
                    24,
                    label,
                    action="agent",
                    payload=("answer", entry.request_id, decision),
                    tooltip=tip,
                )
            )
        layout.height += 34
        layout.pieces.insert(0, Piece("frame", 0, top, width, layout.height - top, color=AMBER))

    def _activity(self, session: ChatSession) -> Layout | None:
        """A trailing "working" line while Claude has nothing visible to show."""
        status: Status = session.status
        if status not in ("starting", "working"):
            return None
        entries: list[Entry] = session.transcript.entries
        if entries and isinstance(entries[-1], AssistantText) and entries[-1].streaming:
            return None
        dots: str = "." * (1 + int(time.monotonic() * 3) % 3)
        label: str = (
            "Starting Claude Code" if status == "starting" else "Thinking" if session.transcript.thinking else "Working"
        )
        layout: Layout = Layout(LINE)
        layout.pieces.append(Piece("text", 0, 0, text=label + dots, color=paint.MUTED))
        return layout

    def _header(self, x: float, top: float, w: float, session: ChatSession, widgets: list[Widget]) -> None:
        y: float = top - HEADER
        paint.rect(x, y, w, HEADER, paint.BUTTON, 4)
        paint.circle(x + 13, y + HEADER / 2, 4, ACCENT, True)
        paint.text("Claude Code", x + 23, y + 9, 13)
        status: Status = session.status
        label: str
        color: paint.Color
        label, color = STATUS[status]
        left: float = x + 31 + paint.measure("Claude Code", 13)
        paint.text(label, left, y + 10, 11, color=color, max_width=max(10, w - (left - x) - 70))
        details: list[str] = []
        account: Account = session.account
        if account.email:
            details.append(account.email + (f" ({account.plan})" if account.plan else ""))
        if session.model:
            details.append(session.model)
        if session.cost:
            details.append(f"${session.cost:.2f} this chat")
        widgets.append(Widget("chat_status", (x, y, w - 92, HEADER), tooltip=" · ".join(details) or "Claude Code"))
        right: float = x + w - 30
        self._button(right, y + 3, 26, 24, "+", "agent", ("new",), "New chat", widgets)
        menu: Rect = (right - 30, y + 3, 26, 24)
        self._frame(menu, "…")
        widgets.append(Widget("chat_menu", menu, "popup", self._menu(session), tooltip="Account and settings"))
        if status != "idle":
            self._button(right - 60, y + 3, 26, 24, "", "agent", ("stop",), "Stop", widgets)
            paint.rect(right - 60 + 9, y + 3 + 8, 8, 8, paint.TEXT, 1)

    @staticmethod
    def _menu(session: ChatSession) -> list[MenuItem]:
        """The … menu: who is signed in, signing in or out, and finding Claude Code."""
        account: Account = session.account
        entries: list[MenuItem] = []
        if account.email:
            entries.append((account.email, "agent", ("account",)))
            if account.plan:
                entries.append((account.plan, "agent", ("account",)))
            entries.append(("Sign out", "agent", ("sign_out",)))
        else:
            entries.append(("Sign in to Claude…", "agent", ("sign_in",)))
        entries.append(("Locate Claude Code…", "agent", ("locate",)))
        entries.append(("Copy MCP command for editors", "window", "mcp_command"))
        return entries

    @staticmethod
    def _dropdown(
        key: str, x: float, y: float, w: float, label: str, entries: list[MenuItem], tip: str, widgets: list[Widget]
    ) -> None:
        paint.rect(x, y, w, TOOLBAR, (0.10, 0.10, 0.10, 1), 5)
        paint.rect(x + 1, y + 1, w - 2, TOOLBAR - 2, (0.17, 0.17, 0.18, 1), 4)
        paint.text(label, x + 8, y + 6, 11, paint.TEXT, max_width=w - 26)
        # A small downward triangle, as on the app's other dropdowns.
        paint.polygon([(x + w - 15, y + 14), (x + w - 7, y + 14), (x + w - 11, y + 9)], paint.MUTED)
        widgets.append(Widget(key, (x, y, w, TOOLBAR), "popup", entries, tooltip=tip))

    def _toolbar(self, x: float, y: float, w: float, session: ChatSession, widgets: list[Widget]) -> None:
        """Permission mode and model choices under the message box, as in the VS Code extension."""
        state: AppState = get_state()
        mode: str = state.agent_mode or session.permission_mode or "default"
        modes: list[MenuItem] = []
        mode_label: str = mode
        value: str
        name: str
        for value, name in MODES:
            modes.append((("✓ " if value == mode else "    ") + name, "agent", ("mode", value)))
            if value == mode:
                mode_label = name
        choices: list[ModelChoice] = []
        choice: ModelChoice
        selected: str = state.agent_model or "default"
        for choice in session.models or list(FALLBACK_MODELS):
            # Aliases follow Claude Code's latest models; a chosen full model name is listed too.
            if not choice.value.startswith("claude-") or choice.value == selected:
                choices.append(choice)
        current: ModelChoice = choices[0] if choices else FALLBACK_MODELS[0]
        for choice in choices:
            if choice.value == selected:
                current = choice
        models: list[MenuItem] = []
        for choice in choices:
            models.append((("✓ " if choice is current else "    ") + choice.label, "agent", ("model", choice.value)))
        effort: str = state.agent_effort
        models.append((("✓ " if not effort else "    ") + "Effort: automatic", "agent", ("effort", "")))
        level: str
        for level in current.efforts or tuple(EFFORTS):
            models.append(
                (
                    ("✓ " if level == effort else "    ") + "Effort: " + EFFORTS.get(level, level),
                    "agent",
                    ("effort", level),
                )
            )
        model_label: str = "Default" if current.value == "default" else current.label
        if effort:
            model_label += " · " + EFFORTS.get(effort, effort)
        half: float = (w - 6) / 2
        self._dropdown("chat_mode", x, y, half, mode_label, modes, "How Claude asks before acting", widgets)
        self._dropdown(
            "chat_model", x + half + 6, y, w - half - 6, model_label, models, current.description or "Model", widgets
        )

    @staticmethod
    def _button(
        x: float,
        y: float,
        w: float,
        h: float,
        label: str,
        action: str,
        payload: object,
        tip: str,
        widgets: list[Widget],
    ) -> None:
        ChatPanel._frame((x, y, w, h), label)
        widgets.append(Widget(f"chat_{action}_{payload}", (x, y, w, h), action, payload, tooltip=tip))

    @staticmethod
    def _frame(rect: Rect, label: str) -> None:
        """A button's face; the caller records what clicking it does."""
        x: float
        y: float
        w: float
        h: float
        x, y, w, h = rect
        paint.rect(x, y, w, h, (0.10, 0.10, 0.10, 1), 5)
        paint.rect(x + 1, y + 1, w - 2, h - 2, (0.20, 0.20, 0.21, 1), 4)
        if label:
            width: float = paint.measure(label, SIZE)
            paint.text(label, x + max(4, (w - width) / 2), y + (h - SIZE) / 2 + 1, SIZE, max_width=w - 8)

    def _input(
        self, x: float, y: float, w: float, edit: Edit | None, session: ChatSession, widgets: list[Widget]
    ) -> float:
        """Draw the message box at the bottom; returns its height."""
        editing: bool = edit is not None and edit.widget.key == "chat_input"
        value: str = edit.text if edit is not None and editing else self.draft
        inner: float = w - 46
        spans: list[tuple[int, int]] = paint.wrap(value, inner, SIZE) if value else [(0, 0)]
        caret: int = edit.caret if edit is not None and editing else len(value)
        caret_line: int = 0
        index: int
        span: tuple[int, int]
        for index, span in enumerate(spans):
            if span[0] <= caret:
                caret_line = index
        first: int = max(0, min(len(spans) - INPUT_LINES, caret_line - INPUT_LINES + 1))
        shown: list[tuple[int, int]] = spans[first : first + INPUT_LINES]
        height: float = len(shown) * LINE + 14
        paint.rect(x, y, w, height, (0.30, 0.42, 0.58, 1) if editing else (0.07, 0.07, 0.075, 1), 6)
        paint.rect(x + 1, y + 1, w - 2, height - 2, FIELD, 5)
        if not value and not editing:
            paint.text(
                "Ask Claude to draw or change the scene…",
                x + 10,
                y + height - 7 - LINE + 3,
                SIZE,
                paint.MUTED,
                max_width=inner,
            )
        top: float = y + height - 7
        if editing and edit is not None and edit.selected and value:
            paint.rect(x + 6, y + 5, inner + 4, height - 10, (0.25, 0.45, 0.65, 0.45), 3)
        for index, span in enumerate(shown):
            line_y: float = top - (index + 1) * LINE + 3
            paint.text(value[span[0] : span[1]].rstrip("\n"), x + 10, line_y, SIZE, paint.TEXT)
            if editing and edit is not None and not edit.selected and first + index == caret_line:
                cursor: float = x + 10 + paint.measure(value[span[0] : min(caret, span[1])], SIZE)
                paint.line([(cursor, line_y - 2), (cursor, line_y + 12)], paint.TEXT, 1.2)
        widgets.append(Widget("chat_input", (x, y, w - 34, height), "agent", value, kind="chat"))
        busy: bool = session.status != "idle"
        button: Rect = (x + w - 31, y + 5, 26, 26)
        paint.rect(button[0], button[1], button[2], button[3], ACCENT if value.strip() or not busy else BUBBLE, 6)
        cx: float = button[0] + button[2] / 2
        cy: float = button[1] + button[3] / 2
        if busy and not value.strip():
            paint.rect(cx - 4.5, cy - 4.5, 9, 9, paint.TEXT, 1)
            widgets.append(Widget("chat_send", button, "agent", ("stop",), tooltip="Stop"))
        else:
            paint.polygon([(cx - 6, cy - 3), (cx + 6, cy - 3), (cx, cy + 5)], (1, 1, 1, 1))
            widgets.append(Widget("chat_send", button, "chat", ("send",), tooltip="Send (Enter)"))
        return height

    def _empty(
        self, x: float, bottom: float, top: float, w: float, session: ChatSession, widgets: list[Widget]
    ) -> None:
        """Introduction and example prompts, or what to do first: locate Claude Code or sign in."""
        if top - bottom < LINE * 2 + 8:
            return
        width: float = w - 2 * PAD
        account: Account = session.account
        if session.missing_cli or account.signed_in is False:
            message: str = (
                "Claude Code was not found on this computer."
                if session.missing_cli
                else "Sign in to Claude to start. Claude Code shares this sign-in with your editor."
            )
            lines: list[str] = self._wrap(message, width)
            line: str
            y_line: float = top - 20
            for line in lines:
                paint.text(line, x + PAD, y_line, SIZE, paint.MUTED, max_width=width)
                y_line -= LINE
            label: str = "Locate Claude Code…" if session.missing_cli else "Sign in to Claude…"
            if session.signing_in:
                label = "Signing in…"
            payload: tuple[str] = ("locate",) if session.missing_cli else ("sign_in",)
            if y_line - 26 >= bottom:
                self._button(x + PAD, y_line - 22, min(width, 170), 26, label, "agent", payload, "", widgets)
            return
        paint.text("Ask Claude to draw, edit, or explain", x + PAD, top - 20, SIZE, paint.MUTED, max_width=width)
        paint.text("your drawing. For example:", x + PAD, top - 20 - LINE, SIZE, paint.MUTED, max_width=width)
        if account.email and bottom + LINE <= top - 22 - LINE * 2:
            signed: str = f"Signed in as {account.email}" + (f" · {account.plan}" if account.plan else "")
            paint.text(signed, x + PAD, bottom + 4, 11, paint.MUTED, max_width=width)
            bottom += LINE
        y: float = top - 22 - LINE * 2
        example: str
        for example in EXAMPLES:
            y -= 28
            if y < bottom:
                return
            paint.rect(x + PAD, y, w - 2 * PAD, 24, BUBBLE, 5)
            paint.text(example, x + PAD + 8, y + 7, SIZE, paint.TEXT, max_width=w - 2 * PAD - 16)
            widgets.append(
                Widget("chat_example_" + example, (x + PAD, y, w - 2 * PAD, 24), "chat", ("example", example))
            )

    def draw(
        self, x: float, y: float, w: float, h: float, edit: Edit | None, hover: tuple[float, float] | None
    ) -> list[Widget]:
        """Draw the panel in (x, y, w, h); returns its widgets."""
        session: ChatSession = agent.session()
        if id(session.transcript) != self._transcript:
            # A new chat starts at its newest message with nothing expanded.
            self._transcript = id(session.transcript)
            self.offset = self._content = 0.0
            self.expanded.clear()
        widgets: list[Widget] = []
        paint.rect(x, y, w, h, BACKGROUND, 4)
        if h < HEADER + TOOLBAR + 70:
            return widgets
        self._header(x + 3, y + h - 3, w - 6, session, widgets)
        self._toolbar(x + 6, y + 6, w - 12, session, widgets)
        input_height: float = self._input(x + 6, y + 6 + TOOLBAR + 4, w - 12, edit, session, widgets)
        bottom: float = y + 6 + TOOLBAR + 4 + input_height + 8
        top: float = y + h - 3 - HEADER - 6
        self.bounds = (x, bottom, w, max(0.0, top - bottom))
        width: float = w - 2 * PAD
        layouts: list[tuple[int, Layout]] = []
        entry: Entry
        for entry in session.transcript.entries:
            layouts.append((entry.id, self._layout(entry, width, session)))
        activity: Layout | None = self._activity(session)
        if activity is not None:
            layouts.append((0, activity))
        if not layouts:
            self._content = self.offset = self.limit = 0.0
            self._empty(x, bottom, top, w, session, widgets)
            return widgets
        total: float = 0.0
        item: tuple[int, Layout]
        for item in layouts:
            total += item[1].height + GAP
        # Keep a scrolled-up view still while new content arrives below it.
        if self.offset > 0.0 and total > self._content:
            self.offset += total - self._content
        self._content = total
        available: float = top - bottom
        self.limit = max(0.0, total - available)
        self.offset = max(0.0, min(self.limit, self.offset))
        cursor: float = top if total <= available else bottom + total - self.offset
        identifier: int
        layout: Layout
        for identifier, layout in layouts:
            self._place(x + PAD, cursor, layout, bottom, top, identifier, hover, widgets)
            cursor -= layout.height + GAP
        if self.limit > 0.0:
            thumb: float = max(20.0, available * available / total)
            position: float = bottom + (available - thumb) * (self.offset / self.limit)
            paint.rect(x + w - 4, position, 3, thumb, (0.50, 0.50, 0.50, 0.6), 1)
        return widgets

    def _place(
        self,
        x: float,
        top: float,
        layout: Layout,
        bottom: float,
        ceiling: float,
        identifier: int,
        hover: tuple[float, float] | None,
        widgets: list[Widget],
    ) -> None:
        """Draw one laid-out entry with its top at `top`, clipped to the transcript area."""
        piece: Piece
        for piece in layout.pieces:
            low: float = top - piece.y - piece.h
            high: float = top - piece.y
            if piece.kind in ("box", "frame"):
                clipped_low: float = max(low, bottom)
                clipped_high: float = min(high, ceiling)
                if clipped_high - clipped_low > 1.0:
                    if piece.kind == "box":
                        paint.rect(x + piece.x, clipped_low, piece.w, clipped_high - clipped_low, piece.color, 4)
                    else:
                        paint.line(
                            [
                                (x + piece.x, clipped_low),
                                (x + piece.x + piece.w, clipped_low),
                                (x + piece.x + piece.w, clipped_high),
                                (x + piece.x, clipped_high),
                            ],
                            piece.color,
                            1.2,
                            closed=True,
                        )
                continue
            if low < bottom - 0.5 or high > ceiling + 0.5:
                continue
            if piece.kind == "text":
                limit: float = piece.w if piece.w else 0.0
                paint.text(piece.text, x + piece.x, low + 3, SIZE, piece.color, max_width=limit)
            elif piece.kind == "dot":
                paint.circle(x + piece.x, low + LINE / 2, 3.5, piece.color, True)
            elif piece.kind == "button":
                rect: Rect = (x + piece.x, low, piece.w, piece.h)
                lit: bool = hover is not None and hit(rect, hover[0], hover[1])
                paint.rect(rect[0], rect[1], rect[2], rect[3], (0.10, 0.10, 0.10, 1), 5)
                paint.rect(
                    rect[0] + 1, rect[1] + 1, rect[2] - 2, rect[3] - 2, (0.28, 0.28, 0.30, 1) if lit else BUBBLE, 4
                )
                width: float = paint.measure(piece.text, SIZE)
                paint.text(
                    piece.text, rect[0] + max(4, (rect[2] - width) / 2), rect[1] + 7, SIZE, max_width=rect[2] - 8
                )
                widgets.append(
                    Widget(f"chat_{identifier}_{piece.text}", rect, piece.action, piece.payload, tooltip=piece.tooltip)
                )
            elif piece.kind == "hit":
                rect = (x + piece.x, low, piece.w, piece.h)
                if hover is not None and hit(rect, hover[0], hover[1]):
                    paint.rect(rect[0] - 3, rect[1], rect[2] + 6, rect[3], (1, 1, 1, 0.05), 3)
                widgets.append(
                    Widget(f"chat_tool_{identifier}", rect, piece.action, piece.payload, tooltip=piece.tooltip)
                )
