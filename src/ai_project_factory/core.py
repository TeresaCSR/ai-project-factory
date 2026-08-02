"""Deterministic core shared by the GUI, CLI, Codex, and Claude adapters."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import urlencode
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


FACTORY_VERSION = "0.5.4"
TEMPLATE_VERSION = "0.1.0-demo"
CONSTITUTION_VERSION = "1.0.0-demo"
PROFILES = ("general", "software", "research")
PROFILE_GUIDANCE = {
    "general": (
        "No domain-specific process is mandatory. Choose verification and "
        "artifacts that fit the actual project."
    ),
    "software": (
        "Prefer executable tests, small reversible changes, and explicit "
        "build/run commands. Do not impose TDD or a framework unless the "
        "Contract requires it."
    ),
    "research": (
        "Preserve data and literature provenance, distinguish observation from "
        "inference, and never convert an unrun simulation or unavailable "
        "artifact into a verified result."
    ),
}

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATE_ROOT = PACKAGE_ROOT / "templates" / "v1"
MEMORY_RUNTIME = PACKAGE_ROOT / "runtime" / "project_memory.py"
REPO_ROOT = PACKAGE_ROOT.parent.parent
SHARED_SKILL_SOURCE = (
    PACKAGE_ROOT / "resources" / "agent-skills" / "ai-project-factory"
)
MULTI_SYNC_JOURNAL = ".ai-project-factory-sync-transaction.json"
MULTI_SYNC_LOCK = ".ai-project-factory-sync.lock"
MULTI_SYNC_SCHEMA = "ai-project-factory/skill-sync-transaction-v1"


class FactoryError(RuntimeError):
    """Raised for safe, user-readable Factory failures."""


@dataclass(frozen=True)
class CreateProjectRequest:
    parent: Path
    project_name: str
    profile: str = "general"
    initialize_git: bool = True
    directory_name: str | None = None


@dataclass(frozen=True)
class ProjectResult:
    project_path: Path
    created_files: tuple[str, ...]
    doctor_output: str


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class CodexLaunchResult:
    project_path: Path
    prompt: str
    deep_link: str


@dataclass(frozen=True)
class CodexTaskResult:
    project_path: Path
    prompt: str
    deep_link: str
    method: str
    thread_id: str | None
    turn_id: str | None
    turn_status: str
    detail: str = ""


@dataclass(frozen=True)
class ProjectSummary:
    project_path: Path
    project_name: str
    profile: str
    mode: str
    goal_status: str
    handoff_revision: int
    factory_version: str
    updated_at: str


AGENT_PROMPTS = {
    "start": (
        "Read AI_START_HERE.md and follow the reading order it gives for the "
        "current mode. In Discussion, check the local facts and begin the "
        "opening interview -- do not invent a contract ahead of it. If the "
        "project is already in Goal, verify HANDOFF and carry on."
    ),
    "prepare": (
        "I am about to compact, switch chats, or switch agents. First update "
        "HANDOFF.md with the substantive progress, evidence, risks, and next "
        "step since the last checkpoint -- not a transcript of the "
        "conversation. Then run the project checkpoint and doctor, and tell "
        "me plainly whether it is safe to switch."
    ),
    "takeover": (
        "Take over this project. Read AI_START_HERE.md first, then verify "
        "PROJECT_CONTRACT, ACTIVE_GOAL, HANDOFF, and the actual artifacts in "
        "that order. Report the current mode, the approved goal, what is "
        "verified, and the next action before doing anything else -- do not "
        "fill gaps from an old chat or from missing information. If the goal "
        "is active, continue once you have reported."
    ),
}


def windows_creation_flags() -> int:
    """Create a real console that Windows keeps hidden from its first frame.

    ``CREATE_NO_WINDOW`` still allowed Codex and some nested Git helpers to
    activate console windows on the user's machine.  ``CREATE_NEW_CONSOLE``
    combined with ``SW_HIDE`` makes every Factory-owned helper hidden from
    creation; descendants can inherit that hidden console.
    """

    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010))


def windows_startup_info() -> object | None:
    """Ask Windows to keep helper windows hidden, including legacy launchers."""

    if os.name != "nt":
        return None
    startup_type = getattr(subprocess, "STARTUPINFO", None)
    if startup_type is None:
        return None
    startup = startup_type()
    startup.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
    startup.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
    return startup


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_directory_name(raw: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", raw.strip())
    value = re.sub(r"\s+", " ", value).rstrip(" .")
    if not value:
        raise FactoryError("The project name does not yield a usable folder name.")
    if value.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        value = f"{value}-project"
    return value


def validate_request(request: CreateProjectRequest) -> tuple[Path, str]:
    name = re.sub(r"\s+", " ", request.project_name.strip())
    if not name:
        raise FactoryError("The project name cannot be empty.")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise FactoryError("The project name cannot contain control characters.")
    if request.profile not in PROFILES:
        raise FactoryError(
            f"Unknown profile: {request.profile}. Choose one of: {', '.join(PROFILES)}"
        )
    parent = request.parent.expanduser().resolve()
    directory_name = safe_directory_name(request.directory_name or name)
    target = parent / directory_name
    if target.exists():
        raise FactoryError(f"Refusing to create over an existing target: {target}")
    return target, name


def render_template(text: str, project_name: str, profile: str) -> str:
    escaped_name = json.dumps(project_name, ensure_ascii=False)[1:-1]
    replacements = {
        "{{PROJECT_NAME}}": escaped_name,
        "{{PROJECT_NAME_JSON}}": escaped_name,
        "{{PROFILE}}": profile,
        "{{PROFILE_GUIDANCE}}": PROFILE_GUIDANCE[profile],
        "{{FACTORY_VERSION}}": FACTORY_VERSION,
        "{{TEMPLATE_VERSION}}": TEMPLATE_VERSION,
        "{{CONSTITUTION_VERSION}}": CONSTITUTION_VERSION,
        "{{TIMESTAMP}}": timestamp(),
    }
    token_pattern = re.compile(r"\{\{[A-Z0-9_]+\}\}")
    unknown = sorted(
        {
            match.group(0)
            for match in token_pattern.finditer(text)
            if match.group(0) not in replacements
        }
    )
    if unknown:
        raise FactoryError(
            "The template has unresolved variables: " + ", ".join(unknown)
        )
    rendered = token_pattern.sub(
        lambda match: replacements[match.group(0)],
        text,
    )
    return rendered.replace("\r\n", "\n")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def copy_templates(staging: Path, project_name: str, profile: str) -> list[str]:
    if not TEMPLATE_ROOT.is_dir():
        raise FactoryError(f"Template folder does not exist: {TEMPLATE_ROOT}")
    created: list[str] = []
    sources = (
        path
        for path in TEMPLATE_ROOT.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix.lower() != ".pyc"
    )
    for source in sorted(sources):
        relative = source.relative_to(TEMPLATE_ROOT)
        destination = staging / relative
        rendered = render_template(
            source.read_text(encoding="utf-8"), project_name, profile
        )
        atomic_write_text(destination, rendered)
        created.append(relative.as_posix())

    runtime_destination = staging / ".ai" / "project_memory.py"
    atomic_write_text(
        runtime_destination,
        MEMORY_RUNTIME.read_text(encoding="utf-8").replace("\r\n", "\n"),
    )
    created.append(".ai/project_memory.py")
    return sorted(created)


def run_command(
    command: list[str], cwd: Path, timeout: int = 60
) -> CommandResult:
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=windows_creation_flags(),
        startupinfo=windows_startup_info(),
    )
    return CommandResult(result.returncode, result.stdout, result.stderr)


def project_runtime(project: Path) -> Path:
    runtime = project.expanduser().resolve() / ".ai" / "project_runtime.py"
    if not runtime.is_file():
        raise FactoryError(f"Not a valid Factory project: missing {runtime}")
    return runtime


def run_project_command(
    project: Path, arguments: Iterable[str], timeout: int = 120
) -> CommandResult:
    root = project.expanduser().resolve()
    runtime = project_runtime(root)
    return run_command([sys.executable, str(runtime), *arguments], root, timeout)


def create_project(request: CreateProjectRequest) -> ProjectResult:
    target, project_name = validate_request(request)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = parent / f".{target.name}.factory-{uuid.uuid4().hex[:10]}"
    if staging.exists():
        raise FactoryError(f"Staging folder unexpectedly exists: {staging}")

    created_files: list[str] = []
    try:
        staging.mkdir()
        created_files = copy_templates(staging, project_name, request.profile)

        if request.initialize_git:
            result = run_command(["git", "init"], staging)
            if not result.ok:
                raise FactoryError("Git init failed:\n" + result.stderr.strip())

        checkpoint = run_project_command(
            staging,
            ["checkpoint", "--updated-by", "factory", "--status", "not_started"],
        )
        if not checkpoint.ok:
            raise FactoryError(
                "The initial handoff checkpoint failed:\n"
                + (checkpoint.stdout + checkpoint.stderr).strip()
            )

        doctor = run_project_command(staging, ["doctor", "--shallow"])
        if not doctor.ok:
            raise FactoryError(
                "The initial project validation failed:\n" + (doctor.stdout + doctor.stderr).strip()
            )

        os.replace(staging, target)
        return ProjectResult(
            project_path=target,
            created_files=tuple(created_files),
            doctor_output=doctor.stdout.strip(),
        )
    except Exception as exc:
        if staging.exists() and staging.parent == parent and ".factory-" in staging.name:
            shutil.rmtree(staging)
        if isinstance(exc, FactoryError):
            raise
        raise FactoryError(str(exc)) from exc


def inspect_project(project: Path) -> CommandResult:
    return run_project_command(project, ["status"])


def doctor_project(project: Path, deep: bool = True) -> CommandResult:
    args = ["doctor"] if deep else ["doctor", "--shallow"]
    return run_project_command(project, args)


def checkpoint_project(
    project: Path, updated_by: str = "factory-gui", status: str | None = None
) -> CommandResult:
    args = ["checkpoint", "--updated-by", updated_by]
    if status:
        args.extend(["--status", status])
    return run_project_command(project, args)


def export_project(project: Path, output: Path | None = None) -> CommandResult:
    args = ["export"]
    if output:
        args.extend(["--output", str(output.expanduser().resolve())])
    return run_project_command(project, args, timeout=180)


def open_in_file_manager(path: Path) -> None:
    target = path.expanduser().resolve()
    if not target.exists():
        raise FactoryError(f"Path does not exist: {target}")
    if os.name == "nt":
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])


def normalize_initial_context(initial_context: str | None) -> str:
    if not initial_context:
        return ""
    value = initial_context.strip()
    if len(value) > 12_000:
        raise FactoryError(
            "The initial idea is too long; keep it under 12000 characters."
        )
    return value


def build_agent_prompt(
    project: Path,
    kind: str = "start",
    initial_context: str | None = None,
) -> str:
    root = project.expanduser().resolve()
    if kind not in AGENT_PROMPTS:
        raise FactoryError(f"Unknown agent prompt kind: {kind}")
    project_runtime(root)
    if not (root / "AI_START_HERE.md").is_file():
        raise FactoryError(
            f"Not a valid Factory project: missing {root / 'AI_START_HERE.md'}"
        )
    context = normalize_initial_context(initial_context)
    prompt = f"Local project folder (readable by this agent): {root}\n\n"
    if kind == "start":
        if context:
            prompt += (
                "The user supplied this initial idea when creating the "
                "project. It is the subject of the opening interview, not an "
                "approved contract, and it does not replace checking the "
                "facts:\n\n"
                f"{context}\n\n"
            )
        prompt += (
            f"{AGENT_PROMPTS[kind]}\n\n"
            "This is real project input sent by AI Project Factory. Do not "
            'skip past it and ask a generic "what would you like to build". '
            'If the previous reply was a Factory start card, the user saying '
            '"continue" means to handle this input now.\n\n'
            "If the project turns out to be in Discussion, first restate -- "
            "concretely and briefly -- what you understand the user actually "
            "wants, then what is confirmed and what is genuinely unknown, "
            "then at most three highest-priority questions, and allow "
            "pushback. If it is in Goal, verify briefly and continue the "
            "approved goal rather than re-running the interview. A missing "
            "optional global memory file or platform adapter is not a "
            "headline result; mention it only if it actually blocks this "
            "project. If the user explicitly asks for a connector, a sign-in, "
            "or another host capability, do it now in this visible session "
            "and handle any approvals through the normal interface."
        )
    else:
        prompt += AGENT_PROMPTS[kind]
    return prompt


def build_codex_deep_link(
    project: Path,
    prompt_kind: str = "start",
    initial_context: str | None = None,
) -> CodexLaunchResult:
    root = project.expanduser().resolve()
    prompt = build_agent_prompt(root, prompt_kind, initial_context)
    query = urlencode({"path": str(root), "prompt": prompt})
    return CodexLaunchResult(
        project_path=root,
        prompt=prompt,
        deep_link=f"codex://threads/new?{query}",
    )


def codex_protocol_registered() -> bool:
    if os.name != "nt":
        return True
    try:
        import winreg
    except ImportError:
        return False
    locations = (
        (winreg.HKEY_CURRENT_USER, r"Software\Classes\codex"),
        (winreg.HKEY_CLASSES_ROOT, r"codex"),
    )
    for hive, subkey in locations:
        try:
            with winreg.OpenKey(hive, subkey):
                return True
        except OSError:
            continue
    return False


def open_codex_deep_link(deep_link: str) -> None:
    if os.name == "nt":
        if not codex_protocol_registered():
            raise FactoryError(
                "No codex:// handler for Codex Desktop was found. "
                "Install or reopen Codex Desktop, then try again."
            )
        try:
            os.startfile(deep_link)  # type: ignore[attr-defined]
        except OSError as exc:
            raise FactoryError(
                "Windows could not invoke Codex Desktop. Check that Codex is installed and opens normally."
            ) from exc
    elif sys.platform == "darwin":
        subprocess.Popen(["open", deep_link])
    else:
        subprocess.Popen(["xdg-open", deep_link])


def launch_codex_draft(
    project: Path,
    prompt_kind: str = "start",
    initial_context: str | None = None,
) -> CodexLaunchResult:
    result = build_codex_deep_link(project, prompt_kind, initial_context)
    open_codex_deep_link(result.deep_link)
    return result


class CodexAppServerClient:
    """Minimal stable-protocol client for one local Codex task."""

    _EOF = object()

    def __init__(self, executable: str, cwd: Path) -> None:
        command = minimal_codex_app_server_command(executable, cwd)
        try:
            self.process = subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=windows_creation_flags(),
                startupinfo=windows_startup_info(),
            )
        except OSError as exc:
            raise FactoryError(
                "Could not start the Codex App Server. Check that the Codex CLI was installed alongside the desktop app."
            ) from exc
        if (
            self.process.stdin is None
            or self.process.stdout is None
            or self.process.stderr is None
        ):
            self.close()
            raise FactoryError("The Codex App Server's stdio is unavailable.")
        self._messages: queue.Queue[object] = queue.Queue()
        self._backlog: list[dict[str, object]] = []
        self._stderr: list[str] = []
        self._write_lock = threading.Lock()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._error_reader = threading.Thread(
            target=self._read_stderr, daemon=True
        )
        self._reader.start()
        self._error_reader.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            for raw in self.process.stdout:
                line = raw.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(message, dict):
                    self._messages.put(message)
        finally:
            self._messages.put(self._EOF)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for raw in self.process.stderr:
            line = raw.rstrip()
            if line:
                self._stderr.append(line)
                if len(self._stderr) > 80:
                    del self._stderr[:20]

    def _send(self, message: dict[str, object]) -> None:
        assert self.process.stdin is not None
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            try:
                self.process.stdin.write(payload + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise FactoryError(self.failure_detail("The Codex App Server disconnected.")) from exc

    def notify(self, method: str, params: dict[str, object]) -> None:
        self._send({"method": method, "params": params})

    def _next_message(self, timeout: float) -> dict[str, object]:
        if self._backlog:
            return self._backlog.pop(0)
        try:
            item = self._messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise FactoryError(
                self.failure_detail("Timed out waiting for the Codex App Server.")
            ) from exc
        if item is self._EOF:
            raise FactoryError(
                self.failure_detail("The Codex App Server exited before finishing the request.")
            )
        return item  # type: ignore[return-value]

    def _reject_server_request(self, message: dict[str, object]) -> None:
        request_id = message.get("id")
        if request_id is None:
            return
        self._send(
            {
                "id": request_id,
                "error": {
                    "code": -32002,
                    "message": (
                        "AI Project Factory does not answer interactive host requests; "
                        "continue with an ordinary reply in the Codex task."
                    ),
                },
            }
        )

    def request(
        self,
        request_id: int,
        method: str,
        params: dict[str, object],
        timeout: float = 30.0,
    ) -> dict[str, object]:
        self._send({"method": method, "id": request_id, "params": params})
        deadline = time.monotonic() + timeout
        deferred: list[dict[str, object]] = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise FactoryError(
                        self.failure_detail(f"Codex request {method} timed out.")
                    )
                message = self._next_message(remaining)
                if (
                    message.get("id") is not None
                    and "method" in message
                    and "result" not in message
                    and "error" not in message
                ):
                    self._reject_server_request(message)
                    continue
                if message.get("id") != request_id:
                    deferred.append(message)
                    continue
                if "error" in message:
                    error = message.get("error")
                    if isinstance(error, dict):
                        detail = str(error.get("message") or error)
                    else:
                        detail = str(error)
                    raise FactoryError(f"Codex request {method} failed: {detail}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise FactoryError(f"Codex request {method} returned an invalid result.")
                return result
        finally:
            if deferred:
                self._backlog[0:0] = deferred

    def wait_for_turn(
        self,
        turn_id: str,
        timeout: float = 900.0,
    ) -> str:
        deadline = time.monotonic() + timeout
        deferred: list[dict[str, object]] = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return "running"
                message = self._next_message(remaining)
                if (
                    message.get("id") is not None
                    and "method" in message
                    and "result" not in message
                    and "error" not in message
                ):
                    self._reject_server_request(message)
                    continue
                method = message.get("method")
                params = message.get("params")
                if not isinstance(params, dict):
                    deferred.append(message)
                    continue
                turn = params.get("turn")
                notification_turn_id = (
                    turn.get("id") if isinstance(turn, dict) else params.get("turnId")
                )
                if method == "turn/completed" and notification_turn_id == turn_id:
                    status = turn.get("status") if isinstance(turn, dict) else None
                    return str(status or "completed")
                deferred.append(message)
        finally:
            if deferred:
                self._backlog[0:0] = deferred

    def failure_detail(self, prefix: str) -> str:
        if not self._stderr:
            return prefix
        return prefix + "\n" + "\n".join(self._stderr[-8:])

    def close(self) -> None:
        process = getattr(self, "process", None)
        if process is None:
            return
        stdin = process.stdin
        if stdin is not None:
            try:
                stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def __enter__(self) -> "CodexAppServerClient":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def minimal_codex_app_server_command(executable: str, cwd: Path) -> list[str]:
    """Build a task-seeding host that starts no user plugin or MCP process."""

    _ = cwd
    feature_args = [
        "--disable",
        "plugins",
        "--disable",
        "apps",
        "--disable",
        "shell_tool",
        "--disable",
        "code_mode_host",
        "--disable",
        "in_app_browser",
    ]
    # These two MCPs are provided by the desktop runtime itself. Plugins and
    # Apps are disabled above, so invoking a second `codex mcp list` process
    # merely to rediscover them would add another Windows console activation.
    names = {"node_repl", "openaiDeveloperDocs"}
    command = [
        executable,
        "app-server",
        "--listen",
        "stdio://",
        *feature_args,
    ]
    for name in sorted(names, key=str.casefold):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            continue
        command.extend(
            ["-c", f"mcp_servers.{name}.enabled=false"]
        )
    return command


def find_codex_executable() -> str:
    executable = shutil.which("codex")
    if executable:
        return executable
    if os.name == "nt":
        candidate = (
            Path.home()
            / "AppData"
            / "Local"
            / "Programs"
            / "OpenAI"
            / "Codex"
            / "bin"
            / "codex.exe"
        )
        if candidate.is_file():
            return str(candidate)
    raise FactoryError(
        "No Codex CLI found. Install or update Codex Desktop, then try again."
    )


def _thread_deep_link(thread_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", thread_id):
        raise FactoryError("Codex returned an invalid task ID.")
    return f"codex://threads/{thread_id}"


def build_codex_quick_start_card(
    project: Path,
    prompt_kind: str,
) -> str:
    """Return an honest deterministic assistant item for an instant handoff."""

    root = project.expanduser().resolve()
    state_label = "not yet verified"
    state_path = root / "AI_PROJECT.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    if isinstance(state, dict):
        mode = state.get("mode")
        goal = state.get("goal_status")
        if isinstance(mode, str) and isinstance(goal, str):
            state_label = f"{mode} / {goal}"
    action = (
        "start the opening interview"
        if prompt_kind == "start"
        else "continue from the takeover prompt above"
    )
    return (
        "AI Project Factory start card "
        "(this is an acknowledgement, not research)\n\n"
        f"- Project: {root.name}\n"
        f"- Local state: {state_label}\n"
        "- Your full project input is in this turn's user message.\n"
        "- This turn only confirms the handoff. No project files were read "
        "and no tools were called.\n\n"
        'Reply "continue" below, or add requirements directly. Codex will '
        f"then {action} in this same visible task, handling any approvals or "
        "sign-ins through the normal interface."
    )


def build_codex_bootstrap_turn_prompt(
    project_prompt: str,
    startup_card: str,
) -> str:
    """Wrap the real task in a visible, bounded bootstrap turn."""

    return (
        "[AI Project Factory visible handoff]\n\n"
        "This is a real Codex user turn. The PROJECT_TASK below must stay in "
        "the transcript so the user can act on it next turn, but this turn "
        "only confirms that the task is ready.\n\n"
        "For this turn: read no files, call no shell, connector, web, or "
        "other tool, and do not analyse or answer the project task. Reply "
        "with the text inside <STARTUP_CARD> verbatim -- no preamble, no "
        "explanation, no conclusion. The restriction applies only to this "
        'confirmation turn; once the user replies "continue" or adds '
        "requirements, carry out <PROJECT_TASK> properly.\n\n"
        f"<STARTUP_CARD>\n{startup_card}\n</STARTUP_CARD>\n\n"
        f"<PROJECT_TASK>\n{project_prompt}\n</PROJECT_TASK>\n\n"
        "To confirm: output STARTUP_CARD only; do not execute PROJECT_TASK."
    )


def launch_codex_task(
    project: Path,
    prompt_kind: str = "start",
    initial_context: str | None = None,
    *,
    on_started: Callable[[str, str], None] | None = None,
    turn_timeout: float = 45.0,
    ephemeral: bool = False,
) -> CodexTaskResult:
    """Start one real visible turn, open Desktop, then finish the handoff."""

    root = project.expanduser().resolve()
    prompt = build_agent_prompt(root, prompt_kind, initial_context)
    quick_start_card = build_codex_quick_start_card(root, prompt_kind)
    bootstrap_prompt = build_codex_bootstrap_turn_prompt(
        prompt,
        quick_start_card,
    )
    executable = find_codex_executable()
    thread_id: str | None = None
    turn_id: str | None = None
    turn_status = "not_started"
    open_detail = ""
    with CodexAppServerClient(executable, root) as client:
        client.request(
            1,
            "initialize",
            {
                "clientInfo": {
                    "name": "ai_project_factory",
                    "title": "AI Project Factory",
                    "version": FACTORY_VERSION,
                },
                "capabilities": {
                    "optOutNotificationMethods": [
                        "item/agentMessage/delta",
                        "item/reasoning/summaryTextDelta",
                        "item/commandExecution/outputDelta",
                    ]
                },
            },
        )
        client.notify("initialized", {})
        started = client.request(
            2,
            "thread/start",
            {
                "cwd": str(root),
                "approvalPolicy": "never",
                "sandbox": "workspace-write",
                "serviceName": "ai_project_factory",
                "ephemeral": ephemeral,
            },
            timeout=60.0,
        )
        thread = started.get("thread")
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
            raise FactoryError("Codex did not return a valid task ID.")
        thread_id = str(thread["id"])
        deep_link = _thread_deep_link(thread_id)
        setup_detail = ""
        try:
            if not ephemeral:
                try:
                    client.request(
                        3,
                        "thread/name/set",
                        {
                            "threadId": thread_id,
                            "name": f"{root.name} · opening discussion",
                        },
                    )
                except FactoryError as exc:
                    setup_detail = (
                        "Task created, but this Codex build did not accept a custom title: "
                        f"{exc}"
                    )
            turn_started = client.request(
                4,
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [
                        {"type": "text", "text": bootstrap_prompt},
                    ],
                    "cwd": str(root),
                    "approvalPolicy": "never",
                    "sandboxPolicy": {
                        "type": "workspaceWrite",
                        "writableRoots": [str(root)],
                        "networkAccess": False,
                    },
                },
                timeout=60.0,
            )
            turn = turn_started.get("turn")
            if not isinstance(turn, dict) or not isinstance(
                turn.get("id"), str
            ):
                raise FactoryError("Codex did not return a valid bootstrap turn ID.")
            turn_id = str(turn["id"])
            open_detail = setup_detail
            if on_started:
                on_started(thread_id, deep_link)
            if not ephemeral:
                try:
                    open_codex_deep_link(deep_link)
                except FactoryError as exc:
                    open_detail = "\n".join(
                        part for part in (open_detail, str(exc)) if part
                    )
            turn_status = client.wait_for_turn(
                turn_id,
                timeout=turn_timeout,
            )
            if turn_status == "running":
                try:
                    client.request(
                        89,
                        "turn/interrupt",
                        {
                            "threadId": thread_id,
                            "turnId": turn_id,
                        },
                        timeout=10.0,
                    )
                except FactoryError:
                    pass
                raise FactoryError(
                    "The visible Codex start card took longer than "
                    f"{turn_timeout:g}s; stopped and fell back to a safe draft."
                )
            if turn_status != "completed":
                raise FactoryError(
                    "The visible Codex start card did not complete: "
                    f"{turn_status}."
                )
        except Exception:
            if not ephemeral:
                try:
                    client.request(
                        90,
                        "thread/delete",
                        {"threadId": thread_id},
                        timeout=20.0,
                    )
                except Exception as cleanup_exc:
                    raise FactoryError(
                        "Codex task initialisation failed and the automatic cleanup did not finish. "
                        f"Check task {thread_id} in Codex.\n{cleanup_exc}"
                    ) from cleanup_exc
            raise

        try:
            client.request(
                91,
                "thread/unsubscribe",
                {"threadId": thread_id},
                timeout=10.0,
            )
        except FactoryError:
            pass
    return CodexTaskResult(
        project_path=root,
        prompt=prompt,
        deep_link=_thread_deep_link(thread_id),
        method="app-server",
        thread_id=thread_id,
        turn_id=turn_id,
        turn_status=turn_status,
        detail=open_detail,
    )


def launch_codex_project(
    project: Path,
    prompt_kind: str = "start",
    initial_context: str | None = None,
    *,
    on_started: Callable[[str, str], None] | None = None,
    turn_timeout: float = 45.0,
) -> CodexTaskResult:
    """Prefer a real task; fall back to an honest prefilled draft."""

    try:
        return launch_codex_task(
            project,
            prompt_kind,
            initial_context,
            on_started=on_started,
            turn_timeout=turn_timeout,
        )
    except FactoryError as exc:
        draft = launch_codex_draft(project, prompt_kind, initial_context)
        return CodexTaskResult(
            project_path=draft.project_path,
            prompt=draft.prompt,
            deep_link=draft.deep_link,
            method="draft",
            thread_id=None,
            turn_id=None,
            turn_status="awaiting_user_send",
            detail=str(exc),
        )


def discover_projects(parent: Path, limit: int = 100) -> tuple[ProjectSummary, ...]:
    root = parent.expanduser().resolve()
    if not root.is_dir() or path_is_link_or_junction(root):
        return ()
    summaries: list[ProjectSummary] = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return ()
    for child in children:
        if len(summaries) >= max(1, limit):
            break
        if path_is_link_or_junction(child) or not child.is_dir():
            continue
        state_path = child / "AI_PROJECT.json"
        runtime = child / ".ai" / "project_runtime.py"
        if not state_path.is_file() or not runtime.is_file():
            continue
        try:
            if state_path.stat().st_size > 256_000:
                continue
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue
        try:
            handoff_revision = int(state.get("handoff_revision") or 0)
        except (TypeError, ValueError):
            handoff_revision = 0
        summaries.append(
            ProjectSummary(
                project_path=child.resolve(),
                project_name=str(state.get("project_name") or child.name),
                profile=str(state.get("profile") or "unknown"),
                mode=str(state.get("mode") or "unknown"),
                goal_status=str(state.get("goal_status") or "unknown"),
                handoff_revision=handoff_revision,
                factory_version=str(state.get("factory_version") or "unknown"),
                updated_at=str(state.get("updated_at") or ""),
            )
        )
    return tuple(
        sorted(
            summaries,
            key=lambda item: (item.updated_at, item.project_name.casefold()),
            reverse=True,
        )
    )


def lexical_absolute(path: Path) -> Path:
    """Normalize a path without following a symlink or Windows junction."""

    return Path(os.path.abspath(os.fspath(path.expanduser())))


def path_is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name == "nt":
        try:
            attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
        except (FileNotFoundError, OSError):
            return False
        reparse_flag = int(
            getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        if attributes & reparse_flag:
            return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def path_entry_exists(path: Path) -> bool:
    return os.path.lexists(path)


def skill_root_sort_key(path: Path) -> tuple[str, str]:
    value = str(path)
    return value.casefold(), value


def tree_hash(root: Path) -> str:
    if path_is_link_or_junction(root) or not root.is_dir():
        raise FactoryError(f"Cannot hash a non-regular directory: {root}")
    digest = hashlib.sha256()

    def add_field(data: bytes) -> None:
        digest.update(len(data).to_bytes(8, byteorder="big", signed=False))
        digest.update(data)

    def visit(directory: Path) -> None:
        entries = sorted(os.scandir(directory), key=lambda item: item.name)
        for entry in entries:
            path = directory / entry.name
            relative = path.relative_to(root).as_posix()
            if path_is_link_or_junction(path):
                raise FactoryError(
                    f"A Factory-managed skill tree cannot contain links or reparse points: {path}"
                )
            if entry.is_dir(follow_symlinks=False):
                add_field(b"D")
                add_field(relative.encode("utf-8"))
                visit(path)
            elif entry.is_file(follow_symlinks=False):
                add_field(b"F")
                add_field(relative.encode("utf-8"))
                executable_bits = path.stat(
                    follow_symlinks=False
                ).st_mode & 0o111
                add_field(str(executable_bits).encode("ascii"))
                add_field(path.read_bytes())
            else:
                raise FactoryError(
                    f"A Factory-managed skill tree contains an unsupported file type: {path}"
                )

    visit(root)
    return digest.hexdigest()


def populate_agent_skill_staging(
    staging: Path,
    actual_factory_root: Path,
    installed_at: str,
) -> None:
    shutil.copytree(
        SHARED_SKILL_SOURCE,
        staging,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    bridge = staging / "scripts" / "factory_bridge.py"
    rendered = bridge.read_text(encoding="utf-8").replace(
        "{{FACTORY_ROOT_JSON}}",
        json.dumps(str(actual_factory_root), ensure_ascii=False)[1:-1],
    )
    rendered = rendered.replace(
        "{{FACTORY_PYTHON_JSON}}",
        json.dumps(sys.executable, ensure_ascii=False)[1:-1],
    )
    atomic_write_text(bridge, rendered)
    marker = {
        "managed_by": "ai-project-factory",
        "factory_version": FACTORY_VERSION,
        "source_hash": tree_hash(SHARED_SKILL_SOURCE),
        "factory_root": str(actual_factory_root),
        "factory_python": sys.executable,
        "installed_at": installed_at,
    }
    atomic_write_text(
        staging / ".factory-managed.json",
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
    )


def expected_agent_skill_hash(
    actual_factory_root: Path,
    installed_at: str,
) -> str:
    with tempfile.TemporaryDirectory(prefix="ai-project-factory-expected-") as raw:
        staging = Path(raw) / "ai-project-factory"
        populate_agent_skill_staging(
            staging,
            actual_factory_root,
            installed_at,
        )
        return tree_hash(staging)


def require_managed_skill_directory(destination: Path) -> dict[str, object]:
    marker_path = destination / ".factory-managed.json"
    if path_is_link_or_junction(destination) or not destination.is_dir():
        raise FactoryError(f"Target skill is not a regular directory; refusing to overwrite: {destination}")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FactoryError(
            f"Target skill has no valid Factory-managed marker; refusing to overwrite: {destination}"
        ) from exc
    if (
        not isinstance(marker, dict)
        or marker.get("managed_by") != "ai-project-factory"
        or not isinstance(marker.get("factory_version"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", str(marker.get("source_hash", "")))
        or not isinstance(marker.get("factory_root"), str)
    ):
        raise FactoryError(
            f"Target skill's Factory-managed marker is invalid; refusing to overwrite: {destination}"
        )
    return marker


def validate_skill_root_location(destination_root: Path) -> Path:
    if not SHARED_SKILL_SOURCE.is_dir():
        raise FactoryError(f"Shared skill source does not exist: {SHARED_SKILL_SOURCE}")
    destination_root = destination_root.expanduser().resolve()
    source_root = SHARED_SKILL_SOURCE.resolve()
    if (
        destination_root == source_root
        or destination_root in source_root.parents
        or source_root in destination_root.parents
    ):
        raise FactoryError(
            "A skill target cannot contain, or be contained by, Factory's canonical skill source: "
            f"{destination_root}"
        )
    return destination_root


def validate_skill_destination(destination_root: Path) -> tuple[Path, Path]:
    destination_root = validate_skill_root_location(destination_root)
    destination = destination_root / "ai-project-factory"
    backups = (
        sorted(destination_root.glob(".ai-project-factory-backup-*"))
        if destination_root.is_dir()
        else []
    )
    if backups and not destination.exists():
        if len(backups) != 1:
            raise FactoryError(
                "Found several interrupted skill backups; refusing to guess which to restore: "
                + ", ".join(str(path) for path in backups)
            )
        require_managed_skill_directory(backups[0])
        os.replace(backups[0], destination)
    elif backups:
        raise FactoryError(
            "Found a leftover skill backup while the target still exists; resolve it by hand first: "
            + ", ".join(str(path) for path in backups)
        )

    if destination.exists():
        require_managed_skill_directory(destination)
    return destination_root, destination


def sync_agent_skill(
    destination_root: Path,
    factory_root: Path | None = None,
    *,
    _installed_at: str | None = None,
    _operation_id: str | None = None,
    _lock_held: bool = False,
    _preserve_backup: bool = False,
) -> Path:
    """Atomically install or refresh the shared thin skill into one runtime."""

    lock_root = validate_skill_root_location(destination_root)
    if not _lock_held:
        with skill_sync_locks((lock_root,)):
            journal_path = lock_root / MULTI_SYNC_JOURNAL
            if path_entry_exists(journal_path):
                raise FactoryError(
                    "An unfinished multi-target skill sync was detected. Retry with all of the original targets; "
                    "do not overwrite the recovery state with a single-target sync."
                )
            return sync_agent_skill(
                lock_root,
                factory_root,
                _installed_at=_installed_at,
                _operation_id=_operation_id,
                _lock_held=True,
                _preserve_backup=_preserve_backup,
            )

    destination_root, destination = validate_skill_destination(destination_root)
    destination_root.mkdir(parents=True, exist_ok=True)

    actual_factory_root = (factory_root or REPO_ROOT).expanduser().resolve()
    operation_id = _operation_id or uuid.uuid4().hex[:10]
    installed_at = _installed_at or timestamp()
    staging = destination_root / f".ai-project-factory-staging-{operation_id}"
    backup = destination_root / f".ai-project-factory-backup-{operation_id}"

    try:
        populate_agent_skill_staging(
            staging,
            actual_factory_root,
            installed_at,
        )

        if destination.exists():
            os.replace(destination, backup)
        os.replace(staging, destination)
        if backup.exists() and not _preserve_backup:
            remove_transaction_path(backup, destination_root)
        return destination
    except Exception as exc:
        if path_entry_exists(staging):
            remove_transaction_path(staging, destination_root)
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        if isinstance(exc, FactoryError):
            raise
        raise FactoryError(str(exc)) from exc


@contextmanager
def skill_sync_locks(
    roots: Iterable[Path],
    timeout_seconds: float = 30.0,
):
    """Serialize a multi-root sync with OS-released locks in stable order."""

    handles: list[object] = []
    locked_roots: list[Path] = []
    try:
        for root in sorted(set(roots), key=skill_root_sort_key):
            root.mkdir(parents=True, exist_ok=True)
            try:
                aliases_existing = any(
                    root.samefile(existing) for existing in locked_roots
                )
            except OSError as exc:
                raise FactoryError(
                    f"Cannot verify the identity of skill target: {root}"
                ) from exc
            if aliases_existing:
                raise FactoryError(
                    "Several skill targets resolve to the same directory: "
                    f"{root}"
                )
            lock_path = root / MULTI_SYNC_LOCK
            if path_is_link_or_junction(lock_path) or (
                lock_path.exists() and not lock_path.is_file()
            ):
                raise FactoryError(f"The skill sync lock is not a regular file: {lock_path}")
            handle = lock_path.open("a+b")
            if lock_path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + timeout_seconds
            acquired = False
            try:
                while not acquired:
                    try:
                        if os.name == "nt":
                            import msvcrt

                            handle.seek(0)
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl

                            fcntl.flock(
                                handle.fileno(),
                                fcntl.LOCK_EX | fcntl.LOCK_NB,
                            )
                        acquired = True
                    except (OSError, BlockingIOError):
                        if time.monotonic() >= deadline:
                            raise FactoryError(
                                "Another Codex/Claude skill sync is still running."
                            )
                        time.sleep(0.05)
            except BaseException:
                handle.close()
                raise
            handles.append(handle)
            locked_roots.append(root)
        yield
    finally:
        for handle in reversed(handles):
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def write_multi_sync_journal(
    roots: tuple[Path, ...],
    journal: dict[str, object],
    phase: str,
) -> None:
    revision = int(journal.get("revision", 0)) + 1
    journal["revision"] = revision
    journal["phase"] = phase
    journal["updated_at"] = timestamp()
    text = json.dumps(journal, ensure_ascii=False, indent=2) + "\n"
    for root in roots:
        atomic_write_text(root / MULTI_SYNC_JOURNAL, text)


def validate_multi_sync_journal(
    journal: dict[str, object],
    roots: tuple[Path, ...],
) -> None:
    if journal.get("schema_version") != MULTI_SYNC_SCHEMA:
        raise FactoryError("The skill sync recovery log has an invalid version.")
    transaction_id = str(journal.get("transaction_id", ""))
    if not re.fullmatch(r"[0-9a-f]{10}", transaction_id):
        raise FactoryError("The skill sync recovery log has an invalid transaction_id.")
    if journal.get("phase") not in {
        "preparing",
        "prepared",
        "committed",
        "rolled_back",
    }:
        raise FactoryError("The skill sync recovery log has an invalid phase.")
    expected_hash = str(journal.get("expected_hash", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise FactoryError("The skill sync recovery log has an invalid expected_hash.")
    targets = journal.get("targets")
    if not isinstance(targets, list) or len(targets) != len(roots):
        raise FactoryError("The skill sync recovery log has an invalid target list.")

    expected_roots = {lexical_absolute(root) for root in roots}
    recorded_roots: set[Path] = set()
    for raw_target in targets:
        if not isinstance(raw_target, dict):
            raise FactoryError("The skill sync recovery log contains an invalid target.")
        root = lexical_absolute(Path(str(raw_target.get("root", ""))))
        if root not in expected_roots or root in recorded_roots:
            raise FactoryError(
                "The recovery log's targets do not match this sync; retry with the original pair of folders."
            )
        recorded_roots.add(root)
        expected_paths = {
            "destination": root / "ai-project-factory",
            "snapshot": root
            / f".ai-project-factory-transaction-{transaction_id}",
            "staging": root / f".ai-project-factory-staging-{transaction_id}",
            "backup": root / f".ai-project-factory-backup-{transaction_id}",
            "recovery": root / f".ai-project-factory-recovery-{transaction_id}",
        }
        for key, expected in expected_paths.items():
            recorded = lexical_absolute(Path(str(raw_target.get(key, ""))))
            if recorded != lexical_absolute(expected):
                raise FactoryError(
                    f"The skill sync recovery log contains an unsafe {key} path."
                )
        original_present = raw_target.get("original_present")
        if not isinstance(original_present, bool):
            raise FactoryError("The skill sync recovery log has an invalid original_present.")
        original_hash = raw_target.get("original_hash")
        if original_present:
            if not isinstance(original_hash, str) or not re.fullmatch(
                r"[0-9a-f]{64}", original_hash
            ):
                raise FactoryError("The skill sync recovery log has an invalid original_hash.")
        elif original_hash is not None:
            raise FactoryError("A skill that did not exist should not carry an original_hash.")
    if recorded_roots != expected_roots:
        raise FactoryError("The skill sync recovery log has no target folder.")


def load_multi_sync_journal(
    roots: tuple[Path, ...],
) -> dict[str, object] | None:
    records: list[dict[str, object]] = []
    for root in roots:
        for residue in root.glob(f".{MULTI_SYNC_JOURNAL}.*.tmp"):
            if residue.is_symlink() or residue.is_file():
                residue.unlink()
        path = root / MULTI_SYNC_JOURNAL
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise FactoryError(f"The skill sync recovery log is not a regular file: {path}")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FactoryError(f"The skill sync recovery log could not be read: {path}") from exc
        if not isinstance(record, dict):
            raise FactoryError(f"The skill sync recovery log is not a JSON object: {path}")
        records.append(record)
    if not records:
        return None
    transaction_ids = {
        str(record.get("transaction_id", "")) for record in records
    }
    if len(transaction_ids) != 1:
        raise FactoryError("Several skill folders hold conflicting recovery logs.")
    revisions: list[int] = []
    for record in records:
        raw_revision = record.get("revision")
        if (
            isinstance(raw_revision, bool)
            or not isinstance(raw_revision, int)
            or raw_revision < 1
        ):
            raise FactoryError("The skill sync recovery log has an invalid revision.")
        revisions.append(raw_revision)
    highest_revision = max(revisions)
    newest = [
        record
        for record, revision in zip(records, revisions, strict=True)
        if revision == highest_revision
    ]
    canonical = json.dumps(newest[0], ensure_ascii=False, sort_keys=True)
    if any(
        json.dumps(record, ensure_ascii=False, sort_keys=True) != canonical
        for record in newest[1:]
    ):
        raise FactoryError("Skill recovery logs at the same revision disagree.")
    validate_multi_sync_journal(newest[0], roots)
    return newest[0]


def transaction_target_path(
    raw_target: dict[str, object],
    key: str,
) -> Path:
    return lexical_absolute(Path(str(raw_target[key])))


def managed_skill_hash(path: Path, label: str) -> str:
    if path_is_link_or_junction(path) or not path.is_dir():
        raise FactoryError(f"{label} is not a regular Factory skill folder: {path}")
    require_managed_skill_directory(path)
    return tree_hash(path)


def optional_managed_skill_hash(path: Path, label: str) -> str | None:
    if not path_entry_exists(path):
        return None
    return managed_skill_hash(path, label)


def optional_transaction_skill_hash(path: Path) -> str | None:
    """Treat a partial transaction-owned tree as unavailable, not authoritative."""

    try:
        return optional_managed_skill_hash(path, "transaction staging skill")
    except FactoryError:
        return None


def remove_link_or_reparse_entry(path: Path) -> None:
    metadata = path.lstat()
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if os.name == "nt" and attributes & reparse_flag and stat.S_ISDIR(
        metadata.st_mode
    ):
        os.rmdir(path)
    else:
        path.unlink()


def remove_owned_tree_without_following_links(path: Path) -> None:
    """Remove one transaction-owned tree without traversing links/junctions."""

    if path_is_link_or_junction(path):
        remove_link_or_reparse_entry(path)
        return
    if not path.is_dir():
        raise FactoryError(f"The transaction staging path is not a directory: {path}")
    for entry in os.scandir(path):
        child = path / entry.name
        if path_is_link_or_junction(child):
            remove_link_or_reparse_entry(child)
        elif entry.is_dir(follow_symlinks=False):
            remove_owned_tree_without_following_links(child)
        else:
            child.unlink()
    path.rmdir()


def remove_transaction_path(path: Path, root: Path) -> None:
    path = lexical_absolute(path)
    root = lexical_absolute(root)
    if lexical_absolute(path.parent) != root:
        raise FactoryError(f"Refusing to clean a path outside the skill root: {path}")
    if not path_entry_exists(path):
        return
    remove_owned_tree_without_following_links(path)


def remove_multi_sync_journals(roots: tuple[Path, ...]) -> None:
    for root in roots:
        path = root / MULTI_SYNC_JOURNAL
        if path.is_symlink():
            raise FactoryError(f"Refusing to delete a symlinked recovery log: {path}")
        path.unlink(missing_ok=True)


def recover_multi_skill_sync(roots: tuple[Path, ...]) -> bool:
    """Recover the newest process-crash journal, if one exists."""

    journal = load_multi_sync_journal(roots)
    if journal is None:
        return False
    phase = str(journal["phase"])
    # A previous phase write can stop between roots. Publish the newest known
    # phase at a higher revision everywhere before mutating any directory, so
    # deleting one journal can never expose an older recovery decision.
    write_multi_sync_journal(roots, journal, phase)
    expected_hash = str(journal["expected_hash"])
    raw_targets = journal["targets"]
    assert isinstance(raw_targets, list)
    states: list[dict[str, object]] = []
    conflicts: list[str] = []

    for raw_target in raw_targets:
        assert isinstance(raw_target, dict)
        root = transaction_target_path(raw_target, "root")
        destination = transaction_target_path(raw_target, "destination")
        snapshot = transaction_target_path(raw_target, "snapshot")
        staging = transaction_target_path(raw_target, "staging")
        backup = transaction_target_path(raw_target, "backup")
        recovery = transaction_target_path(raw_target, "recovery")
        original_present = bool(raw_target["original_present"])
        original_hash = (
            str(raw_target["original_hash"]) if original_present else None
        )
        try:
            destination_hash = optional_managed_skill_hash(
                destination, "current skill"
            )
            if phase != "prepared":
                snapshot_hash = None
                backup_hash = None
                recovery_hash = None
            else:
                snapshot_hash = optional_transaction_skill_hash(snapshot)
                backup_hash = optional_transaction_skill_hash(backup)
                recovery_hash = optional_transaction_skill_hash(recovery)
        except FactoryError as exc:
            conflicts.append(str(exc))
            continue

        if phase == "preparing":
            expected_destination = original_hash if original_present else None
            if destination_hash != expected_destination:
                conflicts.append(
                    f"{destination}: the original target changed during the preparing phase."
                )
            if any(
                path_entry_exists(path)
                for path in (staging, backup, recovery)
            ):
                conflicts.append(
                    f"{destination}: a swap folder appeared during the preparing phase that should not exist."
                )
        elif phase == "prepared":
            if original_present:
                for label, value in (
                    ("snapshot", snapshot_hash),
                    ("backup", backup_hash),
                ):
                    if value not in {None, original_hash}:
                        conflicts.append(
                            f"{destination}: {label} is no longer the version this transaction recorded."
                        )
                if recovery_hash not in {None, expected_hash}:
                    conflicts.append(
                        f"{destination}: recovery is no longer the version this transaction recorded."
                    )
                if original_hash not in {
                    destination_hash,
                    snapshot_hash,
                    backup_hash,
                }:
                    conflicts.append(
                        f"{destination}: no verifiable snapshot of the original skill was found."
                    )
                if destination_hash not in {None, original_hash, expected_hash}:
                    conflicts.append(
                        f"{destination}: the current skill was edited by hand after the crash."
                    )
            else:
                if snapshot_hash is not None or backup_hash is not None:
                    conflicts.append(
                        f"{destination}: a snapshot of an original that never existed showed up."
                    )
                if destination_hash not in {None, expected_hash}:
                    conflicts.append(
                        f"{destination}: the new target was edited by hand after the crash."
                    )
                if recovery_hash not in {None, expected_hash}:
                    conflicts.append(
                        f"{destination}: recovery is no longer the version this transaction recorded."
                    )
        elif phase == "committed":
            if destination_hash != expected_hash:
                conflicts.append(
                    f"{destination}: the committed transaction's new version is missing or altered."
                )
        else:
            expected_destination = original_hash if original_present else None
            if destination_hash != expected_destination:
                conflicts.append(
                    f"{destination}: the rolled-back transaction's original target is missing or altered."
                )

        states.append(
            {
                "root": root,
                "destination": destination,
                "snapshot": snapshot,
                "staging": staging,
                "backup": backup,
                "recovery": recovery,
                "original_present": original_present,
                "original_hash": original_hash,
                "destination_hash": destination_hash,
                "snapshot_hash": snapshot_hash,
                "backup_hash": backup_hash,
                "recovery_hash": recovery_hash,
            }
        )

    if conflicts:
        raise FactoryError(
            "Skill sync recovery stopped: changes outside the transaction were found, and nothing was overwritten.\n- "
            + "\n- ".join(conflicts)
        )

    if phase == "prepared":
        for state in states:
            root = state["root"]
            destination = state["destination"]
            snapshot = state["snapshot"]
            backup = state["backup"]
            recovery = state["recovery"]
            assert isinstance(root, Path)
            assert isinstance(destination, Path)
            assert isinstance(snapshot, Path)
            assert isinstance(backup, Path)
            assert isinstance(recovery, Path)
            destination_hash = state["destination_hash"]
            original_present = bool(state["original_present"])
            original_hash = state["original_hash"]
            if original_present:
                source: Path | None = None
                if state["snapshot_hash"] == original_hash:
                    source = snapshot
                elif state["backup_hash"] == original_hash:
                    source = backup
                if destination_hash == expected_hash:
                    remove_transaction_path(recovery, root)
                    os.replace(destination, recovery)
                    assert source is not None
                    os.replace(source, destination)
                elif destination_hash is None:
                    assert source is not None
                    os.replace(source, destination)
            elif destination_hash == expected_hash:
                remove_transaction_path(recovery, root)
                os.replace(destination, recovery)

    if phase in {"preparing", "prepared"}:
        final_conflicts: list[str] = []
        for state in states:
            destination = state["destination"]
            assert isinstance(destination, Path)
            original_present = bool(state["original_present"])
            expected_destination = (
                state["original_hash"] if original_present else None
            )
            try:
                actual = optional_managed_skill_hash(
                    destination,
                    "rolled-back skill",
                )
            except FactoryError as exc:
                final_conflicts.append(str(exc))
                continue
            if actual != expected_destination:
                final_conflicts.append(
                    f"{destination}: did not return to the version from before the transaction."
                )
        if final_conflicts:
            raise FactoryError(
                "The skill sync rollback did not finish; the recovery log has been kept.\n- "
                + "\n- ".join(final_conflicts)
            )
        write_multi_sync_journal(roots, journal, "rolled_back")
        phase = "rolled_back"

    # committed and rolled_back are terminal cleanup phases. Temporary trees
    # may already be half-deleted by an earlier process kill, so cleanup is
    # deliberately no-follow and does not require their hashes to remain whole.
    for state in states:
        root = state["root"]
        snapshot = state["snapshot"]
        staging = state["staging"]
        backup = state["backup"]
        recovery = state["recovery"]
        assert isinstance(root, Path)
        assert isinstance(snapshot, Path)
        assert isinstance(staging, Path)
        assert isinstance(backup, Path)
        assert isinstance(recovery, Path)
        for temporary in (snapshot, staging, backup, recovery):
            remove_transaction_path(temporary, root)

    remove_multi_sync_journals(roots)
    return True


def sync_agent_skills(
    destination_roots: Iterable[Path],
    factory_root: Path | None = None,
) -> tuple[Path, ...]:
    """Synchronize multiple runtimes with process-crash recovery."""

    roots: list[Path] = []
    seen: set[Path] = set()
    for raw_root in destination_roots:
        root = raw_root.expanduser().resolve()
        if root in seen:
            continue
        seen.add(root)
        roots.append(root)
    if not roots:
        raise FactoryError("Give at least one skill target folder.")
    for index, root in enumerate(roots):
        for other in roots[index + 1 :]:
            if root in other.parents or other in root.parents:
                raise FactoryError(
                    "Skill target folders cannot nest inside one another: "
                    f"{root} ; {other}"
                )
    ordered_roots = tuple(sorted(roots, key=skill_root_sort_key))

    with skill_sync_locks(ordered_roots):
        recover_multi_skill_sync(ordered_roots)
        validated = [
            validate_skill_destination(root) for root in ordered_roots
        ]
        actual_factory_root = (factory_root or REPO_ROOT).expanduser().resolve()
        transaction_id = uuid.uuid4().hex[:10]
        installed_at = timestamp()
        expected_hash = expected_agent_skill_hash(
            actual_factory_root,
            installed_at,
        )
        targets: list[dict[str, object]] = []
        for root, destination in validated:
            original_present = destination.exists()
            targets.append(
                {
                    "root": str(root),
                    "destination": str(destination),
                    "snapshot": str(
                        root
                        / f".ai-project-factory-transaction-{transaction_id}"
                    ),
                    "staging": str(
                        root / f".ai-project-factory-staging-{transaction_id}"
                    ),
                    "backup": str(
                        root / f".ai-project-factory-backup-{transaction_id}"
                    ),
                    "recovery": str(
                        root / f".ai-project-factory-recovery-{transaction_id}"
                    ),
                    "original_present": original_present,
                    "original_hash": (
                        tree_hash(destination) if original_present else None
                    ),
                }
            )
        journal: dict[str, object] = {
            "schema_version": MULTI_SYNC_SCHEMA,
            "transaction_id": transaction_id,
            "revision": 0,
            "phase": "preparing",
            "created_at": timestamp(),
            "factory_root": str(actual_factory_root),
            "factory_python": sys.executable,
            "installed_at": installed_at,
            "expected_hash": expected_hash,
            "targets": targets,
        }

        try:
            write_multi_sync_journal(
                ordered_roots,
                journal,
                "preparing",
            )
            for target in targets:
                if not target["original_present"]:
                    continue
                destination = transaction_target_path(target, "destination")
                snapshot = transaction_target_path(target, "snapshot")
                shutil.copytree(destination, snapshot, symlinks=True)
            write_multi_sync_journal(
                ordered_roots,
                journal,
                "prepared",
            )
            installed: list[Path] = []
            for root, _ in validated:
                destination = sync_agent_skill(
                    root,
                    actual_factory_root,
                    _installed_at=installed_at,
                    _operation_id=transaction_id,
                    _lock_held=True,
                    _preserve_backup=True,
                )
                if tree_hash(destination) != expected_hash:
                    raise FactoryError(
                        f"The synced skill does not match the expected content: {destination}"
                    )
                installed.append(destination)
            write_multi_sync_journal(
                ordered_roots,
                journal,
                "committed",
            )
            for target in targets:
                root = transaction_target_path(target, "root")
                for key in ("snapshot", "staging", "backup", "recovery"):
                    remove_transaction_path(
                        transaction_target_path(target, key),
                        root,
                    )
            remove_multi_sync_journals(ordered_roots)
            return tuple(installed)
        except Exception as exc:
            recovery_error: Exception | None = None
            try:
                recover_multi_skill_sync(ordered_roots)
            except Exception as rollback_exc:
                recovery_error = rollback_exc
            message = str(exc)
            if recovery_error is not None:
                message += (
                    "\nCrash-safe rollback could not complete; transaction "
                    f"journals were preserved:\n{recovery_error}"
                )
            raise FactoryError(message) from exc
