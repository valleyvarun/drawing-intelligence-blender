"""Claude Code chat session and transcript, driven by a stand-in CLI that speaks stream-json."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from collections.abc import Callable
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

if TYPE_CHECKING:
    from drawing_app.agent.attachments import Attachment
    from drawing_app.agent.session import ChatSession, Launch
    from drawing_app.agent.transcript import Transcript

ROOT: Path = Path(__file__).resolve().parents[1]

# A minimal Claude Code: init, streamed text, an optional permission prompt, and turn results.
FAKE_CLI: str = textwrap.dedent("""
    import json, sys
    from pathlib import Path

    if sys.argv[1:3] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "artist@example.com",
                          "subscriptionType": "max"}))
        raise SystemExit(0)
    if sys.argv[1:2] == ["auth"]:
        Path("auth-%s.txt" % sys.argv[2]).write_text("done")
        raise SystemExit(0)
    Path("arguments.json").write_text(json.dumps(sys.argv[1:]))
    MODELS = [
        {"value": "default", "displayName": "Default (recommended)", "description": "Opus 5.5",
         "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
        {"value": "sonnet", "displayName": "Sonnet 5.5", "description": "Efficient",
         "supportedEffortLevels": ["low", "high"]},
        {"value": "claude-opus-4-8", "displayName": "Opus 4.8", "description": "Earlier"},
    ]

    def emit(message):
        sys.stdout.write(json.dumps(message) + "\\n")
        sys.stdout.flush()

    def reply(request_id, response=None):
        emit({"type": "control_response",
              "response": {"subtype": "success", "request_id": request_id, "response": response or {}}})

    def control(message):
        request = message["request"]
        with open("controls.jsonl", "a") as log:
            log.write(json.dumps(request) + "\\n")
        kind = request["subtype"]
        if kind == "initialize":
            reply(message["request_id"], {"account": {"email": "artist@example.com", "subscriptionType": "Claude Max"},
                                          "models": MODELS, "current_permission_mode": "default"})
        elif kind == "set_model":
            reply(message["request_id"], {"model": "claude-" + request["model"]})
        elif kind == "set_permission_mode":
            reply(message["request_id"], {"mode": request["mode"]})
        else:
            reply(message["request_id"])

    def stream(message_id, text):
        emit({"type": "stream_event", "event": {"type": "message_start", "message": {"id": message_id}}})
        emit({"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
              "content_block": {"type": "text", "text": ""}}, "api_message_id": message_id})
        for part in (text[: len(text) // 2], text[len(text) // 2 :]):
            emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                  "delta": {"type": "text_delta", "text": part}}, "api_message_id": message_id})
        emit({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0},
              "api_message_id": message_id})
        emit({"type": "assistant", "message": {"id": message_id, "content": [{"type": "text", "text": text}]}})

    def result(subtype="success"):
        emit({"type": "result", "subtype": subtype, "is_error": subtype != "success", "session_id": "session-1",
              "total_cost_usd": 0.25, "result": ""})

    def ask(request_id):
        emit({"type": "assistant", "message": {"id": "m-" + request_id, "content": [
            {"type": "tool_use", "id": "tool-" + request_id, "name": "Bash", "input": {"command": "echo hi"}}]}})
        emit({"type": "control_request", "request_id": request_id, "request": {
            "subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "echo hi"},
            "description": "Say hi", "tool_use_id": "tool-" + request_id}})

    turn = 0
    lines = iter(sys.stdin)
    for raw in lines:
        message = json.loads(raw)
        if message["type"] == "control_request":
            control(message)
            continue
        turn += 1
        text = message["message"]["content"]
        if isinstance(text, list):
            # Attachments arrive as content blocks; the reply echoes only the typed text.
            Path("content-%d.json" % turn).write_text(json.dumps(text))
            text = " ".join(block["text"] for block in text
                            if block["type"] == "text" and not block["text"].startswith("<attachment"))
        emit({"type": "system", "subtype": "init", "session_id": "session-1", "model": "fake-model"})
        if text.startswith("tool"):
            ask("perm-%d" % turn)
            answer = json.loads(next(lines))
            Path("decision-%d.json" % turn).write_text(json.dumps(answer))
            behavior = answer["response"]["response"]["behavior"]
            emit({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "tool-perm-%d" % turn,
                  "content": "hi" if behavior == "allow" else "denied", "is_error": behavior != "allow"}]}})
            stream("final-%d" % turn, "Done with the tool")
            result()
        elif text == "cancel":
            ask("perm-cancel")
            emit({"type": "control_cancel_request", "request_id": "perm-cancel"})
            result()
        elif text == "wait":
            stream("slow-%d" % turn, "Working on it")
            interrupt = json.loads(next(lines))
            reply(interrupt["request_id"])
            result("error_during_execution")
        else:
            stream("reply-%d" % turn, "Hello there, " + text)
            result()
    """)


def module(name: str, path: str) -> ModuleType:
    spec: ModuleSpec | None = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    result: ModuleType = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {name: result}):
        spec.loader.exec_module(result)
    return result


class AgentChatTests(unittest.TestCase):
    def setUp(self) -> None:
        package: ModuleType = ModuleType("_chat")
        setattr(package, "__path__", [])
        transcript: ModuleType = module("_chat.transcript", "app/drawing_app/agent/transcript.py")
        with patch.dict(sys.modules, {"_chat": package, "_chat.transcript": transcript}):
            attachments: ModuleType = module("_chat.attachments", "app/drawing_app/agent/attachments.py")
            with patch.dict(sys.modules, {"_chat.attachments": attachments}):
                self.session_module: ModuleType = module("_chat.session", "app/drawing_app/agent/session.py")
        self.transcript_module: ModuleType = transcript
        self.attachments: ModuleType = attachments
        self.folder: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.root: Path = Path(self.folder.name)
        (self.root / "claude.py").write_text(FAKE_CLI, encoding="utf-8")
        self.sessions: list[ChatSession] = []

    def tearDown(self) -> None:
        session: ChatSession
        for session in self.sessions:
            session.close()
        self.folder.cleanup()

    def launch(self) -> Launch:
        return cast(
            "Launch",
            self.session_module.Launch(
                command=[sys.executable, str(self.root / "claude.py")],
                cwd=str(self.root),
                mcp_config={"mcpServers": {"blender": {"type": "stdio", "command": "python", "args": ["server.py"]}}},
                allowed_tools=["mcp__blender__scene_info", "mcp__blender__draw_stroke"],
                disallowed_tools=["AskUserQuestion"],
                system_prompt="You are inside a drawing app.",
            ),
        )

    def session(self, prepare: Callable[[], Launch] | None = None) -> ChatSession:
        session: ChatSession = cast("ChatSession", self.session_module.ChatSession(prepare or self.launch))
        self.sessions.append(session)
        return session

    def wait(self, session: ChatSession, condition: Callable[[], bool]) -> None:
        deadline: float = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            session.poll()
            if condition():
                return
            time.sleep(0.01)
        self.fail("Timed out; transcript: " + repr(session.transcript.entries))

    def entries(self, session: ChatSession, kind: str) -> list[object]:
        found: list[object] = []
        entry: object
        for entry in session.transcript.entries:
            if type(entry).__name__ == kind:
                found.append(entry)
        return found

    def test_reply_streams_once_and_reports_model_cost_and_launch_flags(self) -> None:
        session: ChatSession = self.session()
        session.send("  draw a cube  ")
        self.assertIn(session.status, ("starting", "working"))
        self.wait(session, lambda: session.status == "idle")
        texts: list[object] = self.entries(session, "AssistantText")
        self.assertEqual(len(texts), 1)
        self.assertEqual(getattr(texts[0], "text"), "Hello there, draw a cube")
        self.assertFalse(getattr(texts[0], "streaming"))
        self.assertEqual(getattr(self.entries(session, "UserMessage")[0], "text"), "draw a cube")
        self.assertEqual((session.model, session.session_id, session.cost), ("fake-model", "session-1", 0.25))
        arguments: list[str] = json.loads((self.root / "arguments.json").read_text())
        joined: str = " ".join(arguments)
        self.assertIn("--input-format stream-json --output-format stream-json --verbose", joined)
        self.assertIn("--include-partial-messages --permission-prompt-tool stdio", joined)
        servers: object = json.loads(arguments[arguments.index("--mcp-config") + 1])
        self.assertEqual(list(cast(dict[str, dict[str, object]], servers)["mcpServers"]), ["blender"])
        self.assertEqual(
            arguments[arguments.index("--allowedTools") + 1], "mcp__blender__scene_info,mcp__blender__draw_stroke"
        )
        self.assertEqual(arguments[arguments.index("--disallowedTools") + 1], "AskUserQuestion")
        self.assertEqual(arguments[arguments.index("--append-system-prompt") + 1], "You are inside a drawing app.")
        self.assertNotIn("--resume", arguments)
        # The same process carries the conversation on.
        session.send("again")
        self.wait(session, lambda: len(self.entries(session, "AssistantText")) == 2 and session.status == "idle")

    def test_permission_prompts_allow_deny_and_always(self) -> None:
        session: ChatSession = self.session()
        session.send("tool please")
        self.wait(session, lambda: session.status == "waiting")
        approval: object = self.entries(session, "Approval")[0]
        self.assertEqual(getattr(approval, "tool_name"), "Bash")
        self.assertEqual(getattr(approval, "description"), "Say hi")
        session.answer(getattr(approval, "request_id"), "allow")
        self.wait(session, lambda: session.status == "idle")
        decision: dict[str, object] = json.loads((self.root / "decision-1.json").read_text())
        self.assertEqual(
            decision,
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": "perm-1",
                    "response": {"behavior": "allow", "updatedInput": {"command": "echo hi"}},
                },
            },
        )
        tool: object = self.entries(session, "ToolCall")[0]
        self.assertEqual((getattr(tool, "status"), getattr(tool, "result")), ("done", "hi"))
        self.assertEqual(getattr(approval, "status"), "allowed")
        session.send("tool again")
        self.wait(session, lambda: session.status == "waiting")
        second: object = self.entries(session, "Approval")[1]
        session.answer(getattr(second, "request_id"), "deny")
        self.wait(session, lambda: session.status == "idle")
        denied: dict[str, dict[str, dict[str, str]]] = json.loads((self.root / "decision-2.json").read_text())
        self.assertEqual(denied["response"]["response"]["behavior"], "deny")
        self.assertEqual(getattr(self.entries(session, "ToolCall")[1], "status"), "error")
        session.send("tool three")
        self.wait(session, lambda: session.status == "waiting")
        session.answer(getattr(self.entries(session, "Approval")[2], "request_id"), "always")
        self.wait(session, lambda: session.status == "idle")
        # Allowed for the rest of the chat: no new prompt is shown.
        session.send("tool four")
        self.wait(session, lambda: (self.root / "decision-4.json").exists() and session.status == "idle")
        self.assertEqual(len(self.entries(session, "Approval")), 3)

    def test_cancelled_prompt_interrupt_and_new_chat(self) -> None:
        session: ChatSession = self.session()
        session.send("cancel")
        self.wait(session, lambda: session.status == "idle" and bool(self.entries(session, "Approval")))
        self.assertEqual(getattr(self.entries(session, "Approval")[0], "status"), "cancelled")
        session.send("wait")
        self.wait(session, lambda: bool(self.entries(session, "AssistantText")))
        session.interrupt()
        self.assertEqual(session.status, "stopping")
        self.wait(session, lambda: session.status == "idle")
        notices: list[object] = self.entries(session, "Notice")
        self.assertEqual(getattr(notices[-1], "text"), "Stopped.")
        self.assertTrue(session.running)
        session.reset()
        self.assertFalse(session.running)
        self.assertEqual(session.transcript.entries, [])
        self.assertEqual((session.session_id, session.cost), ("", 0.0))
        time.sleep(0.2)
        session.poll()
        # The stopped process's exit is not reported in the new chat.
        self.assertEqual(session.transcript.entries, [])

    def test_resume_after_the_process_ends(self) -> None:
        session: ChatSession = self.session()
        session.send("first")
        self.wait(session, lambda: session.status == "idle")
        session.close()
        session.send("second")
        self.wait(session, lambda: session.status == "idle" and len(self.entries(session, "AssistantText")) == 2)
        arguments: list[str] = json.loads((self.root / "arguments.json").read_text())
        self.assertEqual(arguments[arguments.index("--resume") + 1], "session-1")

    def controls(self) -> list[dict[str, object]]:
        requests: list[dict[str, object]] = []
        path: Path = self.root / "controls.jsonl"
        line: str
        for line in path.read_text().splitlines() if path.exists() else []:
            requests.append(json.loads(line))
        return requests

    def test_warm_start_reports_account_models_and_mode_without_a_message(self) -> None:
        session: ChatSession = self.session()
        session.warm()
        self.wait(session, lambda: bool(session.models))
        self.assertTrue(session.running)
        self.assertEqual(session.status, "idle")
        self.assertEqual(session.transcript.entries, [])
        self.assertEqual(
            (session.account.email, session.account.plan, session.account.signed_in),
            ("artist@example.com", "Claude Max", True),
        )
        labels: list[str] = []
        choice: object
        for choice in session.models:
            labels.append(getattr(choice, "label"))
        self.assertEqual(labels, ["Default (recommended)", "Sonnet 5.5", "Opus 4.8"])
        self.assertEqual(getattr(session.models[1], "efforts"), ("low", "high"))
        self.assertEqual(session.permission_mode, "default")
        self.assertEqual(self.controls(), [{"subtype": "initialize", "hooks": None}])
        # The warm process answers the first message.
        session.send("hello")
        self.wait(session, lambda: session.status == "idle" and bool(self.entries(session, "AssistantText")))

    def test_settings_start_as_flags_and_change_live(self) -> None:
        def launch() -> Launch:
            base: Launch = self.launch()
            return cast(
                "Launch",
                self.session_module.Launch(
                    command=base.command, cwd=base.cwd, model="sonnet", permission_mode="plan", effort="high"
                ),
            )

        session: ChatSession = self.session(launch)
        session.warm()
        self.wait(session, lambda: bool(session.models))
        arguments: list[str] = json.loads((self.root / "arguments.json").read_text())
        self.assertEqual(arguments[arguments.index("--model") + 1], "sonnet")
        self.assertEqual(arguments[arguments.index("--permission-mode") + 1], "plan")
        self.assertEqual(arguments[arguments.index("--effort") + 1], "high")
        session.set_model("haiku")
        session.set_mode("acceptEdits")
        session.set_effort("low")
        session.set_effort("")
        self.wait(session, lambda: session.model == "claude-haiku" and len(self.controls()) == 5)
        self.assertEqual(
            self.controls()[1:],
            [
                {"subtype": "set_model", "model": "haiku"},
                {"subtype": "set_permission_mode", "mode": "acceptEdits"},
                {"subtype": "apply_flag_settings", "settings": {"effortLevel": "low"}},
                {"subtype": "apply_flag_settings", "settings": {"effortLevel": None}},
            ],
        )
        self.assertEqual(session.permission_mode, "acceptEdits")

    def test_attachments_travel_inside_the_message_as_content_blocks(self) -> None:
        session: ChatSession = self.session()
        picture: Attachment = self.attachments.image("sketch.png", b"\x89PNG fake", "image/png", "C:/art/sketch.png")
        notes: Attachment = self.attachments.text("Scene summary", '{"objects": 3}')
        session.attach(picture)
        session.attach(notes)
        session.send("  what is this?  ")
        self.assertEqual(session.attachments, [])
        self.wait(session, lambda: session.status == "idle")
        blocks: list[dict[str, object]] = json.loads((self.root / "content-1.json").read_text())
        kinds: list[object] = [block["type"] for block in blocks]
        self.assertEqual(kinds, ["text", "image", "text", "text"])
        self.assertEqual(blocks[0]["text"], '<attachment name="sketch.png" path="C:/art/sketch.png" />')
        self.assertEqual(blocks[1]["source"], {"type": "base64", "media_type": "image/png", "data": "iVBORyBmYWtl"})
        self.assertEqual(blocks[2]["text"], '<attachment name="Scene summary">\n{"objects": 3}\n</attachment>')
        # The typed text comes last, after what it refers to.
        self.assertEqual(blocks[3], {"type": "text", "text": "what is this?"})
        message: object = self.entries(session, "UserMessage")[0]
        self.assertEqual(
            (getattr(message, "text"), getattr(message, "attachments")),
            ("what is this?", ["sketch.png", "Scene summary"]),
        )
        # Attachments can be sent without any text; plain messages stay plain strings.
        session.attach(notes)
        session.send("")
        self.wait(session, lambda: session.status == "idle" and len(self.entries(session, "UserMessage")) == 2)
        self.assertEqual([block["type"] for block in json.loads((self.root / "content-2.json").read_text())], ["text"])
        session.send("")
        self.assertEqual(len(self.entries(session, "UserMessage")), 2)

    def test_attachments_are_limited_removed_and_forgotten_by_a_new_chat(self) -> None:
        session: ChatSession = self.session()
        notes: Attachment = self.attachments.text("a.txt", "x")
        _index: int
        for _index in range(self.attachments.MAX_ATTACHMENTS):
            session.attach(notes)
        with self.assertRaises(self.attachments.AttachmentError):
            session.attach(notes)
        session.detach(0)
        session.detach(99)
        self.assertEqual(len(session.attachments), self.attachments.MAX_ATTACHMENTS - 1)
        session.reset()
        self.assertEqual(session.attachments, [])

    def test_relaunch_applies_new_launch_settings_and_keeps_the_conversation(self) -> None:
        web: list[bool] = [False]

        def launch() -> Launch:
            base: Launch = self.launch()
            tools: list[str] = ["WebSearch", "WebFetch"]
            return cast(
                "Launch",
                self.session_module.Launch(
                    command=base.command,
                    cwd=base.cwd,
                    allowed_tools=tools if web[0] else [],
                    disallowed_tools=["AskUserQuestion"] + ([] if web[0] else tools),
                ),
            )

        session: ChatSession = self.session(launch)
        session.send("first")
        # Asked during the turn, the restart waits for it to end.
        web[0] = True
        session.relaunch()
        self.wait(session, lambda: session.status == "idle" and bool(self.entries(session, "AssistantText")))
        self.wait(session, lambda: "--resume" in json.loads((self.root / "arguments.json").read_text()))
        arguments: list[str] = json.loads((self.root / "arguments.json").read_text())
        self.assertEqual(arguments[arguments.index("--allowedTools") + 1], "WebSearch,WebFetch")
        self.assertEqual(arguments[arguments.index("--disallowedTools") + 1], "AskUserQuestion")
        self.assertEqual(arguments[arguments.index("--resume") + 1], "session-1")
        self.assertTrue(session.running)
        session.send("second")
        self.wait(session, lambda: len(self.entries(session, "AssistantText")) == 2 and session.status == "idle")
        # Not running: the next start reads the settings anyway.
        session.close()
        session.relaunch()
        self.assertFalse(session.running)

    def test_default_mode_is_not_passed_as_a_flag(self) -> None:
        def launch() -> Launch:
            base: Launch = self.launch()
            return cast(
                "Launch", self.session_module.Launch(command=base.command, cwd=base.cwd, permission_mode="default")
            )

        session: ChatSession = self.session(launch)
        session.warm()
        self.wait(session, lambda: bool(session.models))
        arguments: list[str] = json.loads((self.root / "arguments.json").read_text())
        self.assertNotIn("--permission-mode", arguments)
        self.assertNotIn("--model", arguments)
        self.assertNotIn("--effort", arguments)

    def test_account_status_sign_in_and_sign_out_use_claude_code(self) -> None:
        session: ChatSession = self.session()
        session.refresh_account()
        self.wait(session, lambda: session.account.signed_in is True)
        self.assertEqual((session.account.email, session.account.plan), ("artist@example.com", "Claude Max"))
        self.assertEqual(self.session_module.plan_label("pro"), "Claude Pro")
        self.assertEqual(self.session_module.plan_label("Claude Pro"), "Claude Pro")
        no_window: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        # The sign-in console would flash during tests; the stand-in CLI needs no window.
        with patch.object(subprocess, "CREATE_NEW_CONSOLE", no_window, create=True):
            session.sign_in()
        self.assertTrue(session.signing_in)
        self.wait(session, lambda: not session.signing_in)
        self.assertTrue((self.root / "auth-login.txt").exists())
        session.sign_out()
        self.wait(
            session,
            lambda: bool(self.entries(session, "Notice")) and "Signed out" in str(session.transcript.entries[-1]),
        )

    def test_missing_cli_is_reported_with_a_locate_option(self) -> None:
        def missing() -> Launch:
            raise self.session_module.LaunchError("Claude Code was not found.")

        quiet: ChatSession = self.session(missing)
        quiet.warm()
        # A warm start never adds messages; the panel shows the locate option instead.
        self.assertTrue(quiet.missing_cli)
        self.assertEqual(quiet.transcript.entries, [])
        session: ChatSession = self.session(missing)
        session.send("hello")
        self.assertTrue(session.missing_cli)
        self.assertFalse(session.running)
        self.assertEqual(session.status, "idle")
        notice: object = self.entries(session, "Notice")[0]
        self.assertTrue(getattr(notice, "error"))
        self.assertEqual(getattr(notice, "text"), "Claude Code was not found.")

    def test_transcript_places_stream_events_tools_and_subagents(self) -> None:
        transcript: Transcript = cast("Transcript", self.transcript_module.Transcript())
        events: list[dict[str, object]] = [
            {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "m1"}}},
            {
                "type": "stream_event",
                "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
            },
            {
                "type": "stream_event",
                "event": {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            },
            {
                "type": "stream_event",
                "event": {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Draw"}},
            },
            {"type": "assistant", "message": {"id": "m1", "content": [{"type": "thinking", "thinking": ""}]}},
            {"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": "Drawing now"}]}},
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "index": 2,
                    "content_block": {"type": "tool_use", "id": "t1", "name": "mcp__blender__draw_stroke"},
                },
            },
            {
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "mcp__blender__draw_stroke",
                            "input": {"kind": "pipe", "points": [[0, 0, 0], [1, 0, 0]]},
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "message": {"id": "sub", "content": [{"type": "text", "text": "hidden"}]},
                "parent_tool_use_id": "t1",
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "ok"}]}
                    ]
                },
            },
        ]
        event: dict[str, object]
        for event in events:
            transcript.apply(event)
        kinds: list[str] = []
        entry: object
        for entry in transcript.entries:
            kinds.append(type(entry).__name__)
        self.assertEqual(kinds, ["AssistantText", "ToolCall"])
        self.assertEqual(getattr(transcript.entries[0], "text"), "Drawing now")
        tool: object = transcript.entries[1]
        self.assertEqual((getattr(tool, "status"), getattr(tool, "result")), ("done", "ok"))
        self.assertEqual(self.transcript_module.tool_label("mcp__blender__draw_stroke"), ("draw_stroke", "blender"))
        self.assertEqual(
            self.transcript_module.tool_summary("mcp__blender__draw_stroke", getattr(tool, "input")), "pipe · 2 points"
        )
        self.assertEqual(self.transcript_module.tool_summary("Bash", {"command": "ls   -la"}), "ls -la")
        transcript.add_approval("r1", "Bash", {"command": "rm x"}, "Remove x")
        transcript.apply(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "tool_use", "id": "t2", "name": "Bash"},
                },
            }
        )
        transcript.finish_turn()
        self.assertEqual(getattr(transcript.approval("r1"), "status"), "cancelled")
        self.assertEqual(getattr(transcript.entries[-1], "status"), "stopped")


class AttachmentFileTests(unittest.TestCase):
    """Files from the computer, as agent/attachments.py turns them into content."""

    def setUp(self) -> None:
        package: ModuleType = ModuleType("_files")
        setattr(package, "__path__", [])
        transcript: ModuleType = module("_files.transcript", "app/drawing_app/agent/transcript.py")
        with patch.dict(sys.modules, {"_files": package, "_files.transcript": transcript}):
            self.attachments: ModuleType = module("_files.attachments", "app/drawing_app/agent/attachments.py")
        self.folder: tempfile.TemporaryDirectory[str] = tempfile.TemporaryDirectory()
        self.root: Path = Path(self.folder.name)

    def tearDown(self) -> None:
        self.folder.cleanup()

    def attach(self, name: str, data: bytes) -> Attachment:
        path: Path = self.root / name
        path.write_bytes(data)
        return cast("Attachment", self.attachments.from_file(path))

    def test_each_kind_of_file(self) -> None:
        picture: Attachment = self.attach("photo.JPG", b"\xff\xd8 jpeg")
        self.assertEqual((picture.kind, picture.media_type, picture.name), ("image", "image/jpeg", "photo.JPG"))
        document: Attachment = self.attach("plan.pdf", b"%PDF-1.4 fake")
        self.assertEqual((document.kind, document.media_type), ("document", "application/pdf"))
        self.assertEqual(document.blocks()[0]["title"], "plan.pdf")
        notes: Attachment = self.attach("notes.md", "# Café\nwalls 2.4 m".encode())
        self.assertEqual((notes.kind, notes.data), ("text", "# Café\nwalls 2.4 m"))
        wide: Attachment = self.attach("wide.txt", "hello".encode("utf-16"))
        self.assertEqual(wide.data, "hello")
        # Binary and very long files are named by their path for Claude to open.
        binary: Attachment = self.attach("model.blend", b"BLENDER\x00\x01\x02")
        self.assertEqual((binary.kind, binary.data, binary.path), ("file", "", str(self.root / "model.blend")))
        self.assertIn("binary or too large", str(binary.blocks()[0]["text"]))
        long: Attachment = self.attach("log.txt", b"x" * (self.attachments.TEXT_LIMIT + 1))
        self.assertEqual(long.kind, "file")
        with self.assertRaises(self.attachments.AttachmentError):
            self.attachments.from_file(self.root / "missing.png")

    def test_names_are_escaped_and_plain_messages_stay_text(self) -> None:
        odd: Attachment = self.attachments.text('a "b" <c>.txt', "body")
        self.assertTrue(str(odd.blocks()[0]["text"]).startswith('<attachment name="a &quot;b&quot; &lt;c&gt;.txt">'))
        self.assertEqual(self.attachments.content("hi", []), "hi")
        self.assertEqual(self.attachments.content("", [odd]), odd.blocks())
        with self.assertRaises(self.attachments.AttachmentError):
            self.attachments.image("huge.png", b"x" * (self.attachments.IMAGE_LIMIT + 1), "image/png")


if __name__ == "__main__":
    unittest.main()
