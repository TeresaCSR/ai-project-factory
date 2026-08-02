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


FACTORY_VERSION = "0.5.3"
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
        "请读取 AI_START_HERE.md，并按其中当前模式的读取顺序开始。"
        "如果是 Discussion，请检查本地事实并开始启动访谈；不要提前编造"
        " Contract。若已在 Goal 模式，请核验 HANDOFF 后继续执行。"
    ),
    "prepare": (
        "我要准备 compact、切换聊天或切换 Agent。请先把自上次检查点以来"
        "的实质进展、证据、风险和下一步更新到 HANDOFF.md；不要记录聊天"
        "流水账。随后运行项目 checkpoint 与 doctor，并明确告诉我是否可以"
        "安全切换。"
    ),
    "takeover": (
        "请接管这个项目。先读取 AI_START_HERE.md，按其中顺序核验"
        " PROJECT_CONTRACT、ACTIVE_GOAL、HANDOFF 和实际成果。先报告当前"
        "模式、已批准目标、已验证状态与下一动作；不要根据旧聊天或缺失信息"
        "自行补全。如果 Goal 为 active，报告后继续执行。"
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
        raise FactoryError("项目名称不能生成有效的目录名。")
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
        raise FactoryError("项目名称不能为空。")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise FactoryError("项目名称不能包含控制字符。")
    if request.profile not in PROFILES:
        raise FactoryError(
            f"未知 Profile：{request.profile}。可选值：{', '.join(PROFILES)}"
        )
    parent = request.parent.expanduser().resolve()
    directory_name = safe_directory_name(request.directory_name or name)
    target = parent / directory_name
    if target.exists():
        raise FactoryError(f"目标已存在，为避免覆盖已拒绝创建：{target}")
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
            "模板包含未解析变量：" + ", ".join(unknown)
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
        raise FactoryError(f"模板目录不存在：{TEMPLATE_ROOT}")
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
        raise FactoryError(f"这不是有效的 Factory 项目：缺少 {runtime}")
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
        raise FactoryError(f"临时目录意外存在：{staging}")

    created_files: list[str] = []
    try:
        staging.mkdir()
        created_files = copy_templates(staging, project_name, request.profile)

        if request.initialize_git:
            result = run_command(["git", "init"], staging)
            if not result.ok:
                raise FactoryError("Git 初始化失败：\n" + result.stderr.strip())

        checkpoint = run_project_command(
            staging,
            ["checkpoint", "--updated-by", "factory", "--status", "not_started"],
        )
        if not checkpoint.ok:
            raise FactoryError(
                "初始 Handoff 检查点失败：\n"
                + (checkpoint.stdout + checkpoint.stderr).strip()
            )

        doctor = run_project_command(staging, ["doctor", "--shallow"])
        if not doctor.ok:
            raise FactoryError(
                "初始项目校验失败：\n" + (doctor.stdout + doctor.stderr).strip()
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
        raise FactoryError(f"路径不存在：{target}")
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
        raise FactoryError("初始想法过长，请控制在 12000 个字符以内。")
    return value


def build_agent_prompt(
    project: Path,
    kind: str = "start",
    initial_context: str | None = None,
) -> str:
    root = project.expanduser().resolve()
    if kind not in AGENT_PROMPTS:
        raise FactoryError(f"未知 Agent 提示类型：{kind}")
    project_runtime(root)
    if not (root / "AI_START_HERE.md").is_file():
        raise FactoryError(f"这不是有效的 Factory 项目：缺少 {root / 'AI_START_HERE.md'}")
    context = normalize_initial_context(initial_context)
    prompt = f"本地项目目录（当前 Agent 可访问）：{root}\n\n"
    if kind == "start":
        if context:
            prompt += (
                "用户创建项目时提供的初始想法如下。它是本次启动访谈的主题，"
                "不是已经批准的 Contract，也不能替代事实核验：\n\n"
                f"{context}\n\n"
            )
        prompt += (
            f"{AGENT_PROMPTS[kind]}\n\n"
            "这是 AI Project Factory 发送的真实项目输入，不得忽略后再泛化"
            "询问“你想做什么”。如果上一条回复是 Factory 启动确认，用户"
            "回复“继续”就表示现在正式处理本输入。\n\n"
            "若核验后处于 Discussion，先用简洁、具体的语言复述你对用户真实"
            "意图的理解，再说明已确认事实与关键未知，最后最多提出 3 个最优先"
            "问题，并允许 pushback。若处于 Goal，则简要核验并继续已经批准的"
            " Goal，不要重新做启动访谈。不要把缺少可选的全局记忆文件或平台"
            "适配文件当成主要启动结果；只有它确实阻塞当前项目时才需要告诉"
            "用户。若用户明确要求 Token Bridge、连接器或登录动作，现在由可见"
            "的正常 Codex 宿主执行，并按界面处理必要审批。"
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
                "没有检测到 Codex 桌面版的 codex:// 启动入口。"
                "请先安装或重新打开 Codex 桌面版，再重试。"
            )
        try:
            os.startfile(deep_link)  # type: ignore[attr-defined]
        except OSError as exc:
            raise FactoryError(
                "Windows 未能调用 Codex 桌面版。请确认 Codex 已安装并可正常打开。"
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
                "无法启动 Codex App Server。请确认 Codex CLI 随桌面版正常安装。"
            ) from exc
        if (
            self.process.stdin is None
            or self.process.stdout is None
            or self.process.stderr is None
        ):
            self.close()
            raise FactoryError("Codex App Server 的标准输入输出不可用。")
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
                raise FactoryError(self.failure_detail("Codex App Server 已断开。")) from exc

    def notify(self, method: str, params: dict[str, object]) -> None:
        self._send({"method": method, "params": params})

    def _next_message(self, timeout: float) -> dict[str, object]:
        if self._backlog:
            return self._backlog.pop(0)
        try:
            item = self._messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise FactoryError(
                self.failure_detail("等待 Codex App Server 响应超时。")
            ) from exc
        if item is self._EOF:
            raise FactoryError(
                self.failure_detail("Codex App Server 在完成请求前退出。")
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
                        "AI Project Factory 不处理交互式宿主请求；"
                        "请在 Codex 任务中用普通回复继续。"
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
                        self.failure_detail(f"Codex 请求 {method} 超时。")
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
                    raise FactoryError(f"Codex 请求 {method} 失败：{detail}")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise FactoryError(f"Codex 请求 {method} 返回了无效结果。")
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
        "没有找到 Codex CLI。请先安装或更新 Codex 桌面版，再重试。"
    )


def _thread_deep_link(thread_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", thread_id):
        raise FactoryError("Codex 返回了无效的任务 ID。")
    return f"codex://threads/{thread_id}"


def build_codex_quick_start_card(
    project: Path,
    prompt_kind: str,
) -> str:
    """Return an honest deterministic assistant item for an instant handoff."""

    root = project.expanduser().resolve()
    state_label = "尚未核验"
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
        "开始启动访谈"
        if prompt_kind == "start"
        else "按上一条接管提示继续"
    )
    return (
        "AI Project Factory 启动确认"
        "（这不是项目研究结论）\n\n"
        f"- 项目：{root.name}\n"
        f"- 本地状态：{state_label}\n"
        "- 你的完整项目输入已显示在本轮用户消息中。\n"
        "- 本轮只确认交接，没有读取项目文件或调用工具、Token Bridge、"
        "连接器。\n\n"
        "请在下方回复“继续”或直接补充要求。随后 Codex 会在这个可见任务中"
        f"{action}；如果需要审批或登录，也会由当前 Codex 界面正常处理。"
    )


def build_codex_bootstrap_turn_prompt(
    project_prompt: str,
    startup_card: str,
) -> str:
    """Wrap the real task in a visible, bounded bootstrap turn."""

    return (
        "【AI Project Factory 可见启动交接】\n\n"
        "这是一个真实的 Codex 用户轮次。下面的“项目任务输入”必须保留在"
        "聊天记录中，供用户下一轮继续处理；但当前这一轮只负责确认任务已"
        "就绪。\n\n"
        "本轮不要读取任何文件，不要调用 shell、Token Bridge、连接器、网页"
        "或其他工具，也不要分析或回答项目任务。请仅原样回复"
        " <STARTUP_CARD> 中的文字，不要添加前言、解释或结论。这个限制只"
        "适用于当前启动确认轮；用户下一条回复“继续”或补充要求时，再正式"
        "执行 <PROJECT_TASK>。\n\n"
        f"<STARTUP_CARD>\n{startup_card}\n</STARTUP_CARD>\n\n"
        f"<PROJECT_TASK>\n{project_prompt}\n</PROJECT_TASK>\n\n"
        "再次确认：当前只输出 STARTUP_CARD，不执行 PROJECT_TASK。"
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
            raise FactoryError("Codex 没有返回有效的任务 ID。")
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
                            "name": f"{root.name} · 启动讨论",
                        },
                    )
                except FactoryError as exc:
                    setup_detail = (
                        "任务已创建，但当前 Codex 未接受自定义标题："
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
                raise FactoryError("Codex 没有返回有效的启动轮次 ID。")
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
                    "Codex 可见启动确认超过 "
                    f"{turn_timeout:g} 秒，已停止并退回安全草稿。"
                )
            if turn_status != "completed":
                raise FactoryError(
                    "Codex 可见启动确认没有正常完成："
                    f"{turn_status}。"
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
                        "Codex 任务初始化失败，且自动清理未完成。"
                        f"请在 Codex 中检查任务 {thread_id}。\n{cleanup_exc}"
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
        raise FactoryError(f"无法哈希非普通目录：{root}")
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
                    f"Factory 管理的 Skill 树不能包含链接或 reparse point：{path}"
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
                    f"Factory 管理的 Skill 树包含不支持的文件类型：{path}"
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
        raise FactoryError(f"目标 Skill 不是普通目录，拒绝覆盖：{destination}")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FactoryError(
            f"目标 Skill 缺少有效的 Factory 管理标记，拒绝覆盖：{destination}"
        ) from exc
    if (
        not isinstance(marker, dict)
        or marker.get("managed_by") != "ai-project-factory"
        or not isinstance(marker.get("factory_version"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", str(marker.get("source_hash", "")))
        or not isinstance(marker.get("factory_root"), str)
    ):
        raise FactoryError(
            f"目标 Skill 的 Factory 管理标记无效，拒绝覆盖：{destination}"
        )
    return marker


def validate_skill_root_location(destination_root: Path) -> Path:
    if not SHARED_SKILL_SOURCE.is_dir():
        raise FactoryError(f"共享 Skill 源不存在：{SHARED_SKILL_SOURCE}")
    destination_root = destination_root.expanduser().resolve()
    source_root = SHARED_SKILL_SOURCE.resolve()
    if (
        destination_root == source_root
        or destination_root in source_root.parents
        or source_root in destination_root.parents
    ):
        raise FactoryError(
            "Skill 目标目录不能与 Factory 的规范 Skill 源互相包含："
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
                "发现多个中断的 Skill 备份，拒绝猜测恢复目标："
                + ", ".join(str(path) for path in backups)
            )
        require_managed_skill_directory(backups[0])
        os.replace(backups[0], destination)
    elif backups:
        raise FactoryError(
            "发现残留的 Skill 备份；当前目标仍存在，需先人工确认："
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
                    "检测到未完成的多目标 Skill 同步；请用原来的全部目标重试，"
                    "不要以单目标同步覆盖恢复现场。"
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
                    f"无法验证 Skill 目标目录身份：{root}"
                ) from exc
            if aliases_existing:
                raise FactoryError(
                    "多个 Skill 目标实际指向同一个目录："
                    f"{root}"
                )
            lock_path = root / MULTI_SYNC_LOCK
            if path_is_link_or_junction(lock_path) or (
                lock_path.exists() and not lock_path.is_file()
            ):
                raise FactoryError(f"Skill 同步锁不是普通文件：{lock_path}")
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
                                "另一个 Codex/Claude Skill 同步仍在运行。"
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
        raise FactoryError("Skill 同步恢复日志版本无效。")
    transaction_id = str(journal.get("transaction_id", ""))
    if not re.fullmatch(r"[0-9a-f]{10}", transaction_id):
        raise FactoryError("Skill 同步恢复日志 transaction_id 无效。")
    if journal.get("phase") not in {
        "preparing",
        "prepared",
        "committed",
        "rolled_back",
    }:
        raise FactoryError("Skill 同步恢复日志 phase 无效。")
    expected_hash = str(journal.get("expected_hash", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise FactoryError("Skill 同步恢复日志 expected_hash 无效。")
    targets = journal.get("targets")
    if not isinstance(targets, list) or len(targets) != len(roots):
        raise FactoryError("Skill 同步恢复日志目标列表无效。")

    expected_roots = {lexical_absolute(root) for root in roots}
    recorded_roots: set[Path] = set()
    for raw_target in targets:
        if not isinstance(raw_target, dict):
            raise FactoryError("Skill 同步恢复日志包含无效目标。")
        root = lexical_absolute(Path(str(raw_target.get("root", ""))))
        if root not in expected_roots or root in recorded_roots:
            raise FactoryError(
                "恢复日志目标与本次同步目录不一致；请用原来的两组目录重试。"
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
                    f"Skill 同步恢复日志包含不安全的 {key} 路径。"
                )
        original_present = raw_target.get("original_present")
        if not isinstance(original_present, bool):
            raise FactoryError("Skill 同步恢复日志 original_present 无效。")
        original_hash = raw_target.get("original_hash")
        if original_present:
            if not isinstance(original_hash, str) or not re.fullmatch(
                r"[0-9a-f]{64}", original_hash
            ):
                raise FactoryError("Skill 同步恢复日志 original_hash 无效。")
        elif original_hash is not None:
            raise FactoryError("原本不存在的 Skill 不应带 original_hash。")
    if recorded_roots != expected_roots:
        raise FactoryError("Skill 同步恢复日志缺少目标目录。")


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
            raise FactoryError(f"Skill 同步恢复日志不是普通文件：{path}")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FactoryError(f"Skill 同步恢复日志无法读取：{path}") from exc
        if not isinstance(record, dict):
            raise FactoryError(f"Skill 同步恢复日志不是 JSON object：{path}")
        records.append(record)
    if not records:
        return None
    transaction_ids = {
        str(record.get("transaction_id", "")) for record in records
    }
    if len(transaction_ids) != 1:
        raise FactoryError("多个 Skill 目录包含互相冲突的恢复日志。")
    revisions: list[int] = []
    for record in records:
        raw_revision = record.get("revision")
        if (
            isinstance(raw_revision, bool)
            or not isinstance(raw_revision, int)
            or raw_revision < 1
        ):
            raise FactoryError("Skill 同步恢复日志 revision 无效。")
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
        raise FactoryError("同一 revision 的 Skill 恢复日志内容不一致。")
    validate_multi_sync_journal(newest[0], roots)
    return newest[0]


def transaction_target_path(
    raw_target: dict[str, object],
    key: str,
) -> Path:
    return lexical_absolute(Path(str(raw_target[key])))


def managed_skill_hash(path: Path, label: str) -> str:
    if path_is_link_or_junction(path) or not path.is_dir():
        raise FactoryError(f"{label} 不是普通的 Factory Skill 目录：{path}")
    require_managed_skill_directory(path)
    return tree_hash(path)


def optional_managed_skill_hash(path: Path, label: str) -> str | None:
    if not path_entry_exists(path):
        return None
    return managed_skill_hash(path, label)


def optional_transaction_skill_hash(path: Path) -> str | None:
    """Treat a partial transaction-owned tree as unavailable, not authoritative."""

    try:
        return optional_managed_skill_hash(path, "事务临时 Skill")
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
        raise FactoryError(f"事务临时路径不是目录：{path}")
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
        raise FactoryError(f"拒绝清理 Skill 根目录之外的路径：{path}")
    if not path_entry_exists(path):
        return
    remove_owned_tree_without_following_links(path)


def remove_multi_sync_journals(roots: tuple[Path, ...]) -> None:
    for root in roots:
        path = root / MULTI_SYNC_JOURNAL
        if path.is_symlink():
            raise FactoryError(f"拒绝删除符号链接恢复日志：{path}")
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
                destination, "当前 Skill"
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
                    f"{destination}: preparing 阶段的原目标已被改变。"
                )
            if any(
                path_entry_exists(path)
                for path in (staging, backup, recovery)
            ):
                conflicts.append(
                    f"{destination}: preparing 阶段出现了不应存在的交换目录。"
                )
        elif phase == "prepared":
            if original_present:
                for label, value in (
                    ("snapshot", snapshot_hash),
                    ("backup", backup_hash),
                ):
                    if value not in {None, original_hash}:
                        conflicts.append(
                            f"{destination}: {label} 不再是事务已知版本。"
                        )
                if recovery_hash not in {None, expected_hash}:
                    conflicts.append(
                        f"{destination}: recovery 不再是事务已知版本。"
                    )
                if original_hash not in {
                    destination_hash,
                    snapshot_hash,
                    backup_hash,
                }:
                    conflicts.append(
                        f"{destination}: 找不到可验证的原始 Skill 快照。"
                    )
                if destination_hash not in {None, original_hash, expected_hash}:
                    conflicts.append(
                        f"{destination}: 当前 Skill 在崩溃后被人工修改。"
                    )
            else:
                if snapshot_hash is not None or backup_hash is not None:
                    conflicts.append(
                        f"{destination}: 原本不存在却出现了原始快照。"
                    )
                if destination_hash not in {None, expected_hash}:
                    conflicts.append(
                        f"{destination}: 新目标在崩溃后被人工修改。"
                    )
                if recovery_hash not in {None, expected_hash}:
                    conflicts.append(
                        f"{destination}: recovery 不再是事务已知版本。"
                    )
        elif phase == "committed":
            if destination_hash != expected_hash:
                conflicts.append(
                    f"{destination}: committed 事务的新版本缺失或被修改。"
                )
        else:
            expected_destination = original_hash if original_present else None
            if destination_hash != expected_destination:
                conflicts.append(
                    f"{destination}: rolled_back 事务的原目标缺失或被修改。"
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
            "Skill 同步恢复停止：检测到事务之外的修改，未覆盖任何文件。\n- "
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
                    "回滚后的 Skill",
                )
            except FactoryError as exc:
                final_conflicts.append(str(exc))
                continue
            if actual != expected_destination:
                final_conflicts.append(
                    f"{destination}: 未恢复到事务开始前的版本。"
                )
        if final_conflicts:
            raise FactoryError(
                "Skill 同步回滚尚未完成，恢复日志已保留。\n- "
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
        raise FactoryError("至少指定一个 Skill 目标目录。")
    for index, root in enumerate(roots):
        for other in roots[index + 1 :]:
            if root in other.parents or other in root.parents:
                raise FactoryError(
                    "Skill 目标目录不能互相嵌套："
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
                        f"同步后的 Skill 与预期内容不一致：{destination}"
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
