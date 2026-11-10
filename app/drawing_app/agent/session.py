"""A Claude Code conversation running as a headless CLI process, as the Claude Code VS Code extension runs it.

The CLI speaks newline-delimited JSON on stdio (`--input-format/--output-format stream-json`). User
messages and control requests go to stdin: initialize, interrupt, and the live settings the VS Code
extension also uses (set_model, set_permission_mode, and apply_flag_settings for effort). Events,
permission requests (`can_use_tool`, enabled by `--permission-prompt-tool stdio`), control replies,
and turn results come from stdout. The initialize reply carries the signed-in account and the models
Claude Code offers. Sign-in itself is Claude Code's own (`claude auth login`), shared with the editor.

Reader threads only queue lines; the transcript and settings change on the caller's thread, in poll().
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import IO, Literal, TypeAlias

from .attachments import MAX_ATTACHMENTS, Attachment, AttachmentError, content
from .transcript import Approval, JsonObject, Transcript, items, mapping, text

Status: TypeAlias = Literal["idle", "starting", "working", "waiting", "stopping"]
Decision: TypeAlias = Literal["allow", "always", "deny"]
# Events from the reader threads: a decoded JSON line, or the process exit code.
QueueItem: TypeAlias = tuple[Literal["event"], JsonObject] | tuple[Literal["exit"], int]
# Results of account commands run in the background: status data, or a sign-in/out exit code.
AccountItem: TypeAlias = tuple[Literal["status"], JsonObject] | tuple[Literal["signed_in", "signed_out"], int]

# The CLI gets this long to honour an interrupt before it is stopped.
STOP_GRACE: float = 6.0
# Permission modes offered in the panel, as in the VS Code extension.
MODES: tuple[tuple[str, str], ...] = (
    ("default", "Ask before edits"),
    ("acceptEdits", "Edit automatically"),
    ("plan", "Plan mode"),
)


class LaunchError(Exception):
    """Claude Code cannot be started; the message tells the user what to do."""


@dataclass(frozen=True)
class Launch:
    """How to start Claude Code for this application."""

    command: list[str]
    cwd: str
    mcp_config: JsonObject = field(default_factory=dict)
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    system_prompt: str = ""
    env: dict[str, str] = field(default_factory=dict)
    # Empty values leave Claude Code's own settings in charge.
    model: str = ""
    permission_mode: str = ""
    effort: str = ""


@dataclass(frozen=True)
class Account:
    """The Claude account Claude Code is signed in with; `signed_in` is None until known."""

    email: str = ""
    plan: str = ""
    signed_in: bool | None = None


@dataclass(frozen=True)
class ModelChoice:
    """A model from Claude Code's own list, with the effort levels it supports."""

    value: str
    label: str
    description: str
    efforts: tuple[str, ...]


def plan_label(value: str) -> str:
    """'Claude Pro' from either the initialize reply ('Claude Pro') or auth status ('pro')."""
    if not value or value.lower().startswith("claude"):
        return value
    return "Claude " + value.replace("_", " ").title()


def _account(data: JsonObject) -> Account:
    """An account from `claude auth status --json`."""
    signed: object = data.get("loggedIn")
    plan: str = text(data.get("subscriptionType")) or text(data.get("planTier"))
    if not plan and text(data.get("authMethod")) not in ("", "claude.ai"):
        plan = "API key"
    return Account(text(data.get("email")), plan_label(plan), signed if isinstance(signed, bool) else None)


def _models(value: object) -> list[ModelChoice]:
    choices: list[ModelChoice] = []
    entry: object
    for entry in items(value):
        data: JsonObject = mapping(entry)
        name: str = text(data.get("value"))
        if not name:
            continue
        efforts: list[str] = []
        level: object
        for level in items(data.get("supportedEffortLevels")):
            if isinstance(level, str):
                efforts.append(level)
        choices.append(
            ModelChoice(name, text(data.get("displayName")) or name, text(data.get("description")), tuple(efforts))
        )
    return choices


class ChatSession:
    """One chat with Claude Code. Call poll() regularly on the thread that owns the transcript."""

    def __init__(self, prepare: Callable[[], Launch]) -> None:
        self._prepare: Callable[[], Launch] = prepare
        self.transcript: Transcript = Transcript()
        # The model actually answering, as Claude Code resolves it (for example claude-opus-5-5).
        self.model: str = ""
        self.session_id: str = ""
        self.cost: float = 0.0
        # Claude Code could not be found; the panel offers to locate it.
        self.missing_cli: bool = False
        self.account: Account = Account()
        self.models: list[ModelChoice] = []
        # Claude Code's current permission mode, once known.
        self.permission_mode: str = ""
        self.signing_in: bool = False
        self._process: subprocess.Popen[bytes] | None = None
        self._queue: queue.Queue[QueueItem] = queue.Queue()
        self._accounts: queue.Queue[AccountItem] = queue.Queue()
        self._errors: deque[str] = deque(maxlen=40)
        self._turns: int = 0
        self._starting: bool = False
        self._stop_requested: float = 0.0
        self._requests: int = 0
        # Control request id -> its subtype, until Claude Code replies.
        self._pending: dict[str, str] = {}
        # Conversation to continue when the CLI is started again.
        self._resume: str = ""
        # Tool names the user allowed for the rest of this chat.
        self._allowed: set[str] = set()
        # Files and app context waiting to go with the next message.
        self.attachments: list[Attachment] = []
        # Claude Code restarts with new launch settings once the current turn ends.
        self._relaunch: bool = False

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def status(self) -> Status:
        if self._stop_requested:
            return "stopping"
        if self.transcript.pending():
            return "waiting"
        if self._turns > 0:
            return "starting" if self._starting else "working"
        return "idle"

    def warm(self) -> None:
        """Start Claude Code ahead of the first message, as opening the VS Code panel does.

        No message is sent, so no model is called; the reply fills in the account and model list.
        """
        if not self.running:
            self._start(quiet=True)

    def attach(self, attachment: Attachment) -> None:
        """Add a file or context to the next message."""
        if len(self.attachments) >= MAX_ATTACHMENTS:
            raise AttachmentError(f"A message can carry {MAX_ATTACHMENTS} attachments at most")
        self.attachments.append(attachment)

    def detach(self, index: int) -> None:
        if 0 <= index < len(self.attachments):
            del self.attachments[index]

    def send(self, message: str) -> None:
        """Send a user message with any attachments, starting Claude Code first when needed."""
        value: str = message.strip()
        attached: list[Attachment] = self.attachments
        if not value and not attached:
            return
        self.attachments = []
        names: list[str] = []
        attachment: Attachment
        for attachment in attached:
            names.append(attachment.name)
        self.transcript.add_user(value, names)
        if not self.running and not self._start():
            return
        self._turns += 1
        self._write(
            {
                "type": "user",
                "message": {"role": "user", "content": content(value, attached)},
                "parent_tool_use_id": None,
                "session_id": self.session_id or "default",
            }
        )

    def relaunch(self) -> None:
        """Restart Claude Code with the current launch settings, keeping the conversation.

        A running turn finishes first. Settings that Claude Code only reads at start, such as which tools
        need no approval, change this way.
        """
        if not self.running:
            return
        if self._turns > 0:
            self._relaunch = True
            return
        self._relaunch = False
        self.close()
        self.warm()

    def interrupt(self) -> None:
        """Stop the current turn; the conversation continues with the next message."""
        if not self.running or self._turns == 0 or self._stop_requested:
            return
        self._stop_requested = time.monotonic()
        self._control({"subtype": "interrupt"})

    def set_model(self, value: str) -> None:
        """Switch the model for the rest of this chat (the next start uses the saved choice)."""
        if self.running:
            self._control({"subtype": "set_model", "model": value or "default"})

    def set_mode(self, mode: str) -> None:
        if self.running:
            self.permission_mode = mode
            self._control({"subtype": "set_permission_mode", "mode": mode or "default"})

    def set_effort(self, level: str) -> None:
        if self.running:
            self._control({"subtype": "apply_flag_settings", "settings": {"effortLevel": level or None}})

    def answer(self, request_id: str, decision: Decision) -> None:
        """Reply to a permission prompt shown in the transcript."""
        approval: Approval | None = self.transcript.approval(request_id)
        if approval is None or approval.status != "pending":
            return
        if decision == "deny":
            approval.status = "denied"
            self._permission(request_id, {"behavior": "deny", "message": "The user declined this in the app."})
            return
        approval.status = "allowed"
        if decision == "always":
            self._allowed.add(approval.tool_name)
        self._permission(request_id, {"behavior": "allow", "updatedInput": approval.input})

    def reset(self) -> None:
        """Start a new chat: stop Claude Code and forget the conversation."""
        self.close()
        self.transcript = Transcript()
        self.model = ""
        self.session_id = ""
        self.cost = 0.0
        self._resume = ""
        self._allowed.clear()
        self.attachments = []

    def close(self) -> None:
        """Stop the CLI process; the conversation can be resumed by the next message."""
        process: subprocess.Popen[bytes] | None = self._process
        self._process = None
        # Output still queued by the old process's readers is dropped with its queue.
        self._queue = queue.Queue()
        self._pending.clear()
        self._turns = 0
        self._starting = False
        self._stop_requested = 0.0
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
        self.transcript.finish_turn()

    def refresh_account(self) -> None:
        """Ask Claude Code who is signed in (`claude auth status`), in the background."""
        launch: Launch | None = self._tool(quiet=True)
        if launch is not None:
            threading.Thread(target=self._status, args=(launch,), daemon=True, name="claude-auth").start()

    def sign_in(self) -> None:
        """Run Claude Code's own sign-in in a console window; it opens the browser."""
        if self.signing_in:
            return
        launch: Launch | None = self._tool()
        if launch is None:
            return
        try:
            process: subprocess.Popen[bytes] = subprocess.Popen(
                [*launch.command, "auth", "login"],
                cwd=launch.cwd,
                env=self._environment(launch),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except OSError as error:
            self.transcript.add_notice(f"Claude Code could not start its sign-in: {error}", error=True)
            return
        self.signing_in = True
        self.transcript.add_notice("Finish signing in to Claude in your browser; this chat updates when you are done.")
        threading.Thread(target=self._finish, args=("signed_in", process), daemon=True, name="claude-login").start()

    def sign_out(self) -> None:
        """Sign Claude Code out (`claude auth logout`); editors using Claude Code are signed out too."""
        launch: Launch | None = self._tool()
        if launch is None:
            return
        try:
            process: subprocess.Popen[bytes] = subprocess.Popen(
                [*launch.command, "auth", "logout"],
                cwd=launch.cwd,
                env=self._environment(launch),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            self.transcript.add_notice(f"Claude Code could not sign out: {error}", error=True)
            return
        threading.Thread(target=self._finish, args=("signed_out", process), daemon=True, name="claude-logout").start()

    def poll(self, budget: int = 400) -> bool:
        """Apply queued CLI output and account results; returns whether anything changed."""
        changed: bool = False
        _index: int
        for _index in range(budget):
            try:
                item: QueueItem = self._queue.get_nowait()
            except queue.Empty:
                break
            changed = True
            if item[0] == "event":
                self._handle(item[1])
            else:
                self._exited(item[1])
        while True:
            try:
                result: AccountItem = self._accounts.get_nowait()
            except queue.Empty:
                break
            changed = True
            self._account_result(result)
        if self._stop_requested and time.monotonic() - self._stop_requested > STOP_GRACE and self.running:
            # The CLI ignored the interrupt; stop it and resume the conversation on the next message.
            self.close()
            self.transcript.add_notice("Stopped.")
            changed = True
        return changed

    def _tool(self, quiet: bool = False) -> Launch | None:
        """How to run Claude Code's own commands, such as sign-in; None when it cannot be found.

        Unless quiet, the transcript says why, so a sign-in click never does nothing.
        """
        try:
            launch: Launch = self._prepare()
        except LaunchError as error:
            self.missing_cli = True
            if not quiet:
                self.transcript.add_notice(str(error), error=True)
            return None
        self.missing_cli = False
        os.makedirs(launch.cwd, exist_ok=True)
        return launch

    @staticmethod
    def _environment(launch: Launch) -> dict[str, str]:
        """The environment for every Claude Code command, so sign-in and the chat use the same account."""
        environment: dict[str, str] = dict(os.environ)
        # A Claude Code session that launched this app must not make the CLI think it is nested.
        environment.pop("CLAUDECODE", None)
        environment.update(launch.env)
        return environment

    def _status(self, launch: Launch) -> None:
        data: JsonObject = {}
        try:
            finished: subprocess.CompletedProcess[bytes] = subprocess.run(
                [*launch.command, "auth", "status", "--json"],
                capture_output=True,
                cwd=launch.cwd,
                env=self._environment(launch),
                timeout=30.0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            decoded: object = json.loads(finished.stdout.decode("utf-8", "replace") or "{}")
            data = mapping(decoded)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        self._accounts.put(("status", data))

    def _finish(self, kind: Literal["signed_in", "signed_out"], process: subprocess.Popen[bytes]) -> None:
        self._accounts.put((kind, process.wait()))

    def _account_result(self, result: AccountItem) -> None:
        if result[0] == "status":
            self.account = _account(result[1])
            return
        if result[0] == "signed_in":
            self.signing_in = False
        # Claude Code reads the sign-in when it starts, so the next message starts it again.
        self.close()
        self.refresh_account()
        if result[0] == "signed_out":
            self.transcript.add_notice("Signed out of Claude.")
        elif result[1] != 0:
            self.transcript.add_notice("Sign-in did not finish. Try again from the … menu.", error=True)

    def _start(self, quiet: bool = False) -> bool:
        try:
            launch: Launch = self._prepare()
        except LaunchError as error:
            self.missing_cli = True
            if not quiet:
                self.transcript.add_notice(str(error), error=True)
            return False
        self.missing_cli = False
        arguments: list[str] = [
            *launch.command,
            "-p",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-prompt-tool",
            "stdio",
        ]
        if launch.mcp_config:
            arguments += ["--mcp-config", json.dumps(launch.mcp_config)]
        if launch.allowed_tools:
            arguments += ["--allowedTools", ",".join(launch.allowed_tools)]
        if launch.disallowed_tools:
            arguments += ["--disallowedTools", ",".join(launch.disallowed_tools)]
        if launch.system_prompt:
            arguments += ["--append-system-prompt", launch.system_prompt]
        if launch.model:
            arguments += ["--model", launch.model]
        if launch.permission_mode and launch.permission_mode != "default":
            arguments += ["--permission-mode", launch.permission_mode]
        if launch.effort:
            arguments += ["--effort", launch.effort]
        if self._resume:
            arguments += ["--resume", self._resume]
        environment: dict[str, str] = self._environment(launch)
        os.makedirs(launch.cwd, exist_ok=True)
        try:
            process: subprocess.Popen[bytes] = subprocess.Popen(
                arguments,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=launch.cwd,
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            if not quiet:
                self.transcript.add_notice(f"Claude Code could not start: {error}", error=True)
            return False
        self._process = process
        self._queue = queue.Queue()
        self._errors.clear()
        self._starting = True
        threading.Thread(target=self._read, args=(process, self._queue), daemon=True, name="claude-stdout").start()
        threading.Thread(target=self._read_errors, args=(process,), daemon=True, name="claude-stderr").start()
        self._control({"subtype": "initialize", "hooks": None})
        return True

    def _read(self, process: subprocess.Popen[bytes], target: queue.Queue[QueueItem]) -> None:
        stream: IO[bytes] | None = process.stdout
        if stream is None:
            return
        raw: bytes
        with stream:
            for raw in stream:
                line: str = raw.decode("utf-8", "replace").strip()
                if not line.startswith("{"):
                    continue
                try:
                    decoded: object = json.loads(line)
                except ValueError:
                    continue
                if isinstance(decoded, dict):
                    target.put(("event", decoded))
        target.put(("exit", process.wait()))

    def _read_errors(self, process: subprocess.Popen[bytes]) -> None:
        stream: IO[bytes] | None = process.stderr
        if stream is None:
            return
        raw: bytes
        with stream:
            for raw in stream:
                self._errors.append(raw.decode("utf-8", "replace").rstrip())

    def _write(self, message: JsonObject) -> None:
        process: subprocess.Popen[bytes] | None = self._process
        if process is None or process.stdin is None:
            return
        try:
            process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            process.stdin.flush()
        except OSError as error:
            self.transcript.add_notice(f"Claude Code stopped responding: {error}", error=True)
            self.close()

    def _control(self, request: JsonObject) -> None:
        self._requests += 1
        request_id: str = f"req_{self._requests}_{os.urandom(4).hex()}"
        self._pending[request_id] = text(request.get("subtype"))
        self._write({"type": "control_request", "request_id": request_id, "request": request})

    def _permission(self, request_id: str, response: JsonObject) -> None:
        self._write(
            {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": request_id, "response": response},
            }
        )

    def _handle(self, event: JsonObject) -> None:
        kind: object = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self._starting = False
            self.model = text(event.get("model")) or self.model
            self.session_id = text(event.get("session_id")) or self.session_id
            self.permission_mode = text(event.get("permissionMode")) or self.permission_mode
        elif kind == "control_request":
            self._request(text(event.get("request_id")), mapping(event.get("request")))
        elif kind == "control_cancel_request":
            approval: Approval | None = self.transcript.approval(text(event.get("request_id")))
            if approval is not None and approval.status == "pending":
                approval.status = "cancelled"
        elif kind == "control_response":
            self._reply(mapping(event.get("response")))
        elif kind == "result":
            self._result(event)
        else:
            if kind in ("stream_event", "assistant"):
                self._starting = False
            self.transcript.apply(event)

    def _reply(self, response: JsonObject) -> None:
        """A reply to one of this session's control requests."""
        subtype: str = self._pending.pop(text(response.get("request_id")), "")
        if response.get("subtype") == "error":
            self.transcript.add_notice("Claude Code: " + text(response.get("error")), error=True)
            return
        data: JsonObject = mapping(response.get("response"))
        if subtype == "initialize":
            account: JsonObject = mapping(data.get("account"))
            email: str = text(account.get("email"))
            if email:
                self.account = Account(email, plan_label(text(account.get("subscriptionType"))), True)
            else:
                # No account in the reply: ask Claude Code whether anyone is signed in.
                self.refresh_account()
            self.models = _models(data.get("models")) or self.models
            self.permission_mode = text(data.get("current_permission_mode")) or self.permission_mode
        elif subtype == "set_model":
            self.model = text(data.get("model")) or self.model
        elif subtype == "set_permission_mode":
            self.permission_mode = text(data.get("mode")) or self.permission_mode

    def _request(self, request_id: str, request: JsonObject) -> None:
        if request.get("subtype") != "can_use_tool":
            # Hook and SDK-server callbacks are not used by this host.
            self._write(
                {
                    "type": "control_response",
                    "response": {"subtype": "error", "request_id": request_id, "error": "Not supported by this app."},
                }
            )
            return
        name: str = text(request.get("tool_name"))
        tool_input: JsonObject = mapping(request.get("input"))
        if name in self._allowed:
            self._permission(request_id, {"behavior": "allow", "updatedInput": tool_input})
            return
        description: str = text(request.get("description")) or text(request.get("title"))
        self.transcript.add_approval(request_id, name, tool_input, description)

    def _result(self, event: JsonObject) -> None:
        self._turns = max(0, self._turns - 1)
        self._starting = False
        self.session_id = text(event.get("session_id")) or self.session_id
        self._resume = self.session_id
        cost: object = event.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            self.cost = float(cost)
        stopped: bool = bool(self._stop_requested)
        if self._turns == 0:
            self._stop_requested = 0.0
            self.transcript.finish_turn()
            if self._relaunch:
                self.relaunch()
        if stopped:
            self.transcript.add_notice("Stopped.")
        elif event.get("is_error") is True or event.get("subtype") not in (None, "success"):
            detail: str = text(event.get("result")) or text(event.get("subtype")).replace("_", " ")
            self.transcript.add_notice("Claude Code: " + (detail or "the turn ended with an error"), error=True)
            lowered: str = detail.lower()
            if "login" in lowered or "log in" in lowered or "authenticat" in lowered or "401" in lowered:
                # Show the sign-in option if the account was signed out or expired.
                self.refresh_account()

    def _exited(self, code: int) -> None:
        # Each process has its own queue, so this exit is the current process's.
        self._process = None
        self._pending.clear()
        busy: bool = self._turns > 0
        self._turns = 0
        self._starting = False
        self._stop_requested = 0.0
        self.transcript.finish_turn()
        if busy or code != 0:
            details: str = "\n".join(list(self._errors)[-6:]).strip()
            message: str = f"Claude Code exited (code {code})."
            self.transcript.add_notice(message + ("\n" + details if details else ""), error=True)
