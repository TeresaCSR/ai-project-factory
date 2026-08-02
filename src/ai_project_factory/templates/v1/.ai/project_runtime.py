#!/usr/bin/env python3
"""Model-neutral lifecycle runtime embedded in every Factory project."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "AI_PROJECT.json"
CONTRACT_PATH = ROOT / "PROJECT_CONTRACT.md"
GOAL_PATH = ROOT / "ACTIVE_GOAL.md"
HANDOFF_PATH = ROOT / "HANDOFF.md"
MEMORY_TOOL = ROOT / ".ai" / "project_memory.py"
JOURNAL_PATH = ROOT / ".ai" / "lifecycle_transaction.json"
LOCK_PATH = ROOT / ".ai" / "lifecycle.lock"
RUNTIME_TEMP_DIR = ROOT / ".ai" / "runtime-tmp"
_LOCK_DEPTH = 0

VALID_MODES = {"discussion", "goal"}
VALID_GOAL_STATUSES = {
    "none",
    "active",
    "paused",
    "blocked",
    "completed",
    "needs_revision",
}

REQUIRED_FILES = (
    "AI_START_HERE.md",
    "CONSTITUTION.md",
    "PROJECT_CONTRACT.md",
    "PROJECT_CONTEXT.md",
    "ACTIVE_GOAL.md",
    "AI_PROJECT.json",
    "HANDOFF.md",
    "DECISIONS.md",
    "ARTIFACTS.md",
    "MIGRATION_PROMPT.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".ai/project_memory.py",
    ".ai/project_runtime.py",
)


class ProjectStateError(RuntimeError):
    """Raised when a lifecycle operation would create invalid project state."""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def document_fingerprint(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_fingerprint(path: Path) -> str:
    return document_fingerprint(path.read_text(encoding="utf-8"))


def ensure_runtime_temp_dir() -> Path:
    ai_dir = (ROOT / ".ai").resolve()
    if RUNTIME_TEMP_DIR.exists() and (
        not RUNTIME_TEMP_DIR.is_dir()
        or RUNTIME_TEMP_DIR.is_symlink()
        or RUNTIME_TEMP_DIR.resolve().parent != ai_dir
    ):
        raise ProjectStateError(
            ".ai/runtime-tmp must be a real directory inside this project."
        )
    RUNTIME_TEMP_DIR.mkdir(parents=False, exist_ok=True)
    if RUNTIME_TEMP_DIR.resolve().parent != ai_dir:
        raise ProjectStateError(
            ".ai/runtime-tmp resolves outside this project's .ai directory."
        )
    return RUNTIME_TEMP_DIR


def cleanup_runtime_temps() -> None:
    temp_dir = ensure_runtime_temp_dir()
    for candidate in temp_dir.iterdir():
        if (
            candidate.name.startswith(".")
            and candidate.name.endswith(".tmp")
            and (candidate.is_file() or candidate.is_symlink())
        ):
            candidate.unlink()


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path != JOURNAL_PATH and JOURNAL_PATH.is_file():
        record_transaction_planned_write(path, text)
    temp_dir = ensure_runtime_temp_dir()
    fd, raw_temp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(temp_dir)
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


def record_transaction_planned_write(path: Path, text: str) -> None:
    journal = json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
    if journal.get("phase") != "prepared":
        return
    try:
        relative = path.relative_to(ROOT).as_posix()
    except ValueError:
        return
    originals = journal.get("originals", {})
    if relative not in originals:
        return
    fingerprints = journal.setdefault("written_fingerprints", {})
    recorded = fingerprints.setdefault(relative, [])
    planned = document_fingerprint(text)
    if planned not in recorded:
        recorded.append(planned)
    atomic_write(
        JOURNAL_PATH,
        json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
    )


def recover_lifecycle_transaction() -> None:
    if not JOURNAL_PATH.is_file():
        return
    try:
        journal = json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectStateError(
            f"Lifecycle transaction journal is unreadable: {JOURNAL_PATH}"
        ) from exc
    if not isinstance(journal, dict):
        raise ProjectStateError("Lifecycle transaction journal must be an object.")
    phase = journal.get("phase")
    if phase == "prepared":
        originals = journal.get("originals")
        if not isinstance(originals, dict):
            raise ProjectStateError(
                "Lifecycle transaction journal has no recovery snapshot."
            )
        written_fingerprints = journal.get("written_fingerprints", {})
        if not isinstance(written_fingerprints, dict):
            raise ProjectStateError(
                "Lifecycle transaction journal has invalid write fingerprints."
            )
        allowed = {
            STATE_PATH.relative_to(ROOT).as_posix(),
            CONTRACT_PATH.relative_to(ROOT).as_posix(),
            GOAL_PATH.relative_to(ROOT).as_posix(),
            HANDOFF_PATH.relative_to(ROOT).as_posix(),
        }
        conflicts: list[str] = []
        for relative, text in originals.items():
            if relative not in allowed or not isinstance(text, str):
                raise ProjectStateError(
                    "Lifecycle transaction journal contains an unsafe recovery path."
                )
            path = ROOT / relative
            current_fingerprint = file_fingerprint(path)
            known = {document_fingerprint(text)}
            recorded = written_fingerprints.get(relative, [])
            if isinstance(recorded, list):
                known.update(
                    item for item in recorded if isinstance(item, str)
                )
            if current_fingerprint not in known:
                conflicts.append(relative)
        if conflicts:
            raise ProjectStateError(
                "Lifecycle recovery stopped because files were edited after the "
                "interrupted transaction: "
                + ", ".join(conflicts)
                + ". The journal and current files were preserved for manual review."
            )
        for relative, text in originals.items():
            atomic_write(ROOT / relative, text)
    elif phase != "committed":
        raise ProjectStateError(
            f"Unknown lifecycle transaction phase: {phase}"
        )
    JOURNAL_PATH.unlink()


def begin_lifecycle_transaction(name: str, paths: tuple[Path, ...]) -> None:
    recover_lifecycle_transaction()
    originals = {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in paths
    }
    payload = {
        "schema_version": "ai-project-factory/transaction-v1",
        "transaction": name,
        "phase": "prepared",
        "started_at": now(),
        "originals": originals,
        "written_fingerprints": {},
    }
    atomic_write(
        JOURNAL_PATH,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def commit_lifecycle_transaction() -> None:
    if not JOURNAL_PATH.is_file():
        raise ProjectStateError("Lifecycle transaction journal is missing.")
    journal = json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
    journal["phase"] = "committed"
    journal["committed_at"] = now()
    atomic_write(
        JOURNAL_PATH,
        json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
    )
    JOURNAL_PATH.unlink()


def abort_lifecycle_transaction() -> None:
    recover_lifecycle_transaction()


@contextmanager
def project_lock(timeout_seconds: float = 30.0):
    global _LOCK_DEPTH
    if _LOCK_DEPTH > 0:
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
        return

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+b")
    if LOCK_PATH.stat().st_size == 0:
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

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise ProjectStateError(
                        "Another project lifecycle command is still running."
                    )
                time.sleep(0.05)
        _LOCK_DEPTH = 1
        cleanup_runtime_temps()
        yield
    finally:
        if acquired:
            _LOCK_DEPTH = 0
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
        else:
            handle.close()


@contextmanager
def lifecycle_transaction(name: str, paths: tuple[Path, ...]):
    with project_lock():
        begin_lifecycle_transaction(name, paths)
        try:
            yield
        except BaseException:
            abort_lifecycle_transaction()
            raise
        else:
            commit_lifecycle_transaction()


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectStateError("AI_PROJECT.json is missing.") from exc
    except json.JSONDecodeError as exc:
        raise ProjectStateError(f"AI_PROJECT.json is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise ProjectStateError("AI_PROJECT.json must contain a JSON object.")
    return data


def write_state(state: dict[str, Any]) -> None:
    atomic_write(
        STATE_PATH,
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
    )


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    normalized = text.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        raise ProjectStateError("Markdown file is missing YAML-style front matter.")
    end = normalized.find("\n---\n", 4)
    if end < 0:
        raise ProjectStateError("Markdown front matter is not closed.")
    header = normalized[4:end]
    body = normalized[end + 5 :]
    values: dict[str, str] = {}
    for line in header.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ProjectStateError(f"Invalid front-matter line: {line}")
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values, body


def serialize_front_matter(values: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key, value in values.items():
        if value is None:
            rendered = "null"
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    lines.extend(["---", "", body.lstrip("\n")])
    return "\n".join(lines).rstrip() + "\n"


def load_markdown(path: Path) -> tuple[dict[str, str], str, str]:
    text = path.read_text(encoding="utf-8")
    metadata, body = parse_front_matter(text)
    return metadata, body, text


def import_memory_tool():
    if not MEMORY_TOOL.is_file():
        raise ProjectStateError(".ai/project_memory.py is missing.")
    spec = importlib.util.spec_from_file_location("_factory_project_memory", MEMORY_TOOL)
    if spec is None or spec.loader is None:
        raise ProjectStateError("Cannot load .ai/project_memory.py.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def current_fingerprints() -> tuple[str, str, str]:
    tool = import_memory_tool()
    base_revision, workspace_fingerprint = tool.compute_fingerprint(ROOT)
    context_fingerprint = tool.compute_context_fingerprint(ROOT)
    return base_revision, workspace_fingerprint, context_fingerprint


def normalized_scalar(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip().strip('"').strip("'").lower()


def positive_int(value: str | int | None) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def critical_placeholders(text: str) -> list[str]:
    markers = ("[TBD]", "[NONE]", "[TO FILL]", "{{")
    return [marker for marker in markers if marker in text]


def markdown_section(text: str, heading: str) -> str:
    marker = f"## {heading}\n"
    start = text.find(marker)
    if start < 0:
        raise ProjectStateError(f"Missing section: {heading}")
    content_start = start + len(marker)
    next_heading = text.find("\n## ", content_start)
    if next_heading < 0:
        next_heading = len(text)
    return text[content_start:next_heading].strip()


def replace_markdown_section(text: str, heading: str, content: str) -> str:
    marker = f"## {heading}\n"
    start = text.find(marker)
    if start < 0:
        raise ProjectStateError(f"Missing section: {heading}")
    content_start = start + len(marker)
    next_heading = text.find("\n## ", content_start)
    if next_heading < 0:
        next_heading = len(text)
    replacement = "\n" + content.strip() + "\n"
    return text[:content_start] + replacement + text[next_heading:]


def sync_handoff_for_goal(goal_id: str) -> str:
    _, goal_body, _ = load_markdown(GOAL_PATH)
    handoff_meta, handoff_body, original = load_markdown(HANDOFF_PATH)
    objective = markdown_section(goal_body, "Objective")
    next_actions = markdown_section(goal_body, "Next actions")
    confirmed = (
        "### Completed\n\n"
        "- Kickoff discussion was materialized and approved.\n\n"
        "### In progress\n\n"
        f"- Active Goal `{goal_id}` is executing.\n\n"
        "### Blocked\n\n"
        "- None known."
    )
    handoff_body = replace_markdown_section(
        handoff_body, "Current objective", objective
    )
    handoff_body = replace_markdown_section(
        handoff_body, "Confirmed state", confirmed
    )
    handoff_body = replace_markdown_section(
        handoff_body, "Next actions", next_actions
    )
    handoff_body = replace_markdown_section(
        handoff_body, "User decisions required", "- None."
    )
    atomic_write(HANDOFF_PATH, serialize_front_matter(handoff_meta, handoff_body))
    return original


def sync_handoff_for_exit(goal_status: str, reason: str) -> str:
    metadata, body, original = load_markdown(HANDOFF_PATH)
    goal_id = metadata.get("active_goal_id", "unknown")
    label = goal_status.replace("_", " ")
    objective = f"Goal `{goal_id}` is {label}."
    body = replace_markdown_section(body, "Current objective", objective)
    if goal_status == "completed":
        confirmed = (
            "### Completed\n\n"
            "- Kickoff discussion was materialized and approved.\n"
            f"- Goal `{goal_id}` completed: {reason}\n\n"
            "### In progress\n\n"
            "- None. The project is back in Discussion mode.\n\n"
            "### Blocked\n\n"
            "- None known."
        )
        next_actions = (
            "1. Review the verified deliverables.\n"
            "2. Discuss or define the next bounded Goal if more work is desired."
        )
        user_decision = "- None required for the completed Goal."
    elif goal_status == "needs_revision":
        confirmed = (
            "### Completed\n\n"
            "- Kickoff discussion was materialized and approved.\n\n"
            "### In progress\n\n"
            f"- Goal `{goal_id}` stopped because its Contract needs revision.\n\n"
            "### Blocked\n\n"
            f"- Contract invalidation: {reason}"
        )
        next_actions = (
            f"1. Revise the Contract and Goal to address: {reason}\n"
            "2. Obtain approval and commit a new Discussion baseline."
        )
        user_decision = f"- Approve the revised Contract after addressing: {reason}"
    else:
        state_word = "blocked" if goal_status == "blocked" else "paused"
        confirmed = (
            "### Completed\n\n"
            "- Kickoff discussion was materialized and approved.\n\n"
            "### In progress\n\n"
            f"- Goal `{goal_id}` is {state_word}; no execution should continue yet.\n\n"
            "### Blocked\n\n"
            f"- {reason}"
        )
        next_actions = (
            f"1. Resolve or discuss: {reason}\n"
            "2. Resume only after the recorded condition is addressed."
        )
        user_decision = f"- {reason}"
    body = replace_markdown_section(body, "Confirmed state", confirmed)
    body = replace_markdown_section(body, "Next actions", next_actions)
    body = replace_markdown_section(
        body, "User decisions required", user_decision
    )
    atomic_write(HANDOFF_PATH, serialize_front_matter(metadata, body))
    return original


def require_document_binding(
    state: dict[str, Any],
    *,
    label: str,
    document_revision: int,
    document_text: str,
    state_revision_key: str,
    state_fingerprint_key: str,
    allow_revision_increment: bool = False,
) -> str:
    state_revision = positive_int(state.get(state_revision_key))
    state_fingerprint = str(state.get(state_fingerprint_key) or "")
    current_fingerprint = document_fingerprint(document_text)

    if document_revision == state_revision:
        if not state_fingerprint:
            raise ProjectStateError(
                f"{label} has no bound fingerprint in AI_PROJECT.json."
            )
        if current_fingerprint != state_fingerprint:
            raise ProjectStateError(
                f"{label} content changed without incrementing its revision."
            )
    elif not (
        allow_revision_increment and document_revision == state_revision + 1
    ):
        expected = (
            f"{state_revision} or {state_revision + 1}"
            if allow_revision_increment
            else str(state_revision)
        )
        raise ProjectStateError(
            f"{label} revision must be {expected}; found {document_revision}."
        )
    return current_fingerprint


def validate_transition_documents() -> tuple[dict[str, str], dict[str, str]]:
    contract_meta, _, contract_text = load_markdown(CONTRACT_PATH)
    goal_meta, _, goal_text = load_markdown(GOAL_PATH)

    problems: list[str] = []
    if normalized_scalar(contract_meta.get("status")) != "baselined":
        problems.append("PROJECT_CONTRACT.md status must be BASELINED.")
    if positive_int(contract_meta.get("contract_revision")) < 1:
        problems.append("PROJECT_CONTRACT.md contract_revision must be at least 1.")
    if normalized_scalar(contract_meta.get("approved_by")) in {"", "null", "none"}:
        problems.append("PROJECT_CONTRACT.md approved_by must name the approver.")
    if critical_placeholders(contract_text):
        problems.append("PROJECT_CONTRACT.md still contains unresolved placeholders.")

    if normalized_scalar(goal_meta.get("status")) != "active":
        problems.append("ACTIVE_GOAL.md status must be ACTIVE.")
    if normalized_scalar(goal_meta.get("goal_id")) in {"", "none", "null"}:
        problems.append("ACTIVE_GOAL.md goal_id must be set.")
    if positive_int(goal_meta.get("goal_revision")) < 1:
        problems.append("ACTIVE_GOAL.md goal_revision must be at least 1.")
    if critical_placeholders(goal_text):
        problems.append("ACTIVE_GOAL.md still contains unresolved placeholders.")

    if problems:
        raise ProjectStateError("\n".join(problems))
    return contract_meta, goal_meta


def update_handoff_metadata(
    state: dict[str, Any], updated_by: str, status: str | None = None
) -> dict[str, Any]:
    original_state = STATE_PATH.read_text(encoding="utf-8")
    metadata, body, original_handoff = load_markdown(HANDOFF_PATH)
    revision = max(
        positive_int(metadata.get("handoff_revision")),
        positive_int(state.get("handoff_revision")),
    ) + 1

    try:
        state["handoff_revision"] = revision
        state["updated_at"] = now()
        write_state(state)

        base_revision, workspace_fingerprint, context_fingerprint = (
            current_fingerprints()
        )
        metadata.update(
            {
                "handoff_revision": str(revision),
                "updated_at": state["updated_at"],
                "updated_by": updated_by,
                "base_revision": base_revision,
                "workspace_fingerprint": workspace_fingerprint,
                "context_fingerprint": context_fingerprint,
                "status": status or str(state.get("goal_status", "unknown")),
                "mode": str(state.get("mode", "unknown")),
                "goal_status": str(state.get("goal_status", "unknown")),
                "active_goal_id": state.get("active_goal_id") or "none",
            }
        )
        atomic_write(HANDOFF_PATH, serialize_front_matter(metadata, body))
        return state
    except Exception:
        atomic_write(STATE_PATH, original_state)
        atomic_write(HANDOFF_PATH, original_handoff)
        raise


def checkpoint(updated_by: str, status: str | None = None) -> dict[str, Any]:
    with lifecycle_transaction("checkpoint", (STATE_PATH, HANDOFF_PATH)):
        state = load_state()
        return update_handoff_metadata(
            state, updated_by=updated_by, status=status
        )


def _commit_discussion_impl(updated_by: str) -> dict[str, Any]:
    state = load_state()
    if state.get("mode") != "discussion":
        raise ProjectStateError("Discussion can be committed only from discussion mode.")

    contract_meta, goal_meta = validate_transition_documents()
    _, _, contract_text = load_markdown(CONTRACT_PATH)
    contract_fingerprint = require_document_binding(
        state,
        label="PROJECT_CONTRACT.md",
        document_revision=positive_int(contract_meta["contract_revision"]),
        document_text=contract_text,
        state_revision_key="contract_revision",
        state_fingerprint_key="contract_fingerprint",
        allow_revision_increment=True,
    )
    previous_state = dict(state)
    original_handoff = sync_handoff_for_goal(goal_meta["goal_id"].strip())
    state.update(
        {
            "mode": "goal",
            "goal_status": "active",
            "active_goal_id": goal_meta["goal_id"].strip(),
            "contract_revision": positive_int(contract_meta["contract_revision"]),
            "active_goal_revision": positive_int(goal_meta["goal_revision"]),
            "contract_fingerprint": contract_fingerprint,
            "active_goal_fingerprint": file_fingerprint(GOAL_PATH),
            "last_transition": "discussion_committed",
            "pause_reason": None,
            "updated_at": now(),
        }
    )
    try:
        write_state(state)
        return update_handoff_metadata(state, updated_by, status="active")
    except Exception:
        atomic_write(HANDOFF_PATH, original_handoff)
        write_state(previous_state)
        raise


def commit_discussion(updated_by: str) -> dict[str, Any]:
    with lifecycle_transaction(
        "commit_discussion", (STATE_PATH, HANDOFF_PATH)
    ):
        return _commit_discussion_impl(updated_by)


def set_goal_markdown_status(
    new_status: str, reason: str = ""
) -> tuple[str, dict[str, str]]:
    metadata, body, original = load_markdown(GOAL_PATH)
    clean_reason = " ".join(reason.split()).strip()
    if new_status == "completed":
        body = insert_section_bullet(
            body,
            "Progress and evidence",
            f"Goal completed: {clean_reason}",
        )
        body = replace_markdown_section(body, "Blockers", "- None known.")
        body = replace_markdown_section(
            body,
            "Next actions",
            "1. No further execution under this Goal.\n"
            "2. Discuss and approve a new bounded Goal if more work is desired.",
        )
    elif new_status == "needs_revision":
        body = insert_section_bullet(
            body,
            "Progress and evidence",
            f"Goal stopped because the Contract needs revision: {clean_reason}",
        )
        body = replace_markdown_section(
            body, "Blockers", f"- Contract invalidation: {clean_reason}"
        )
        body = replace_markdown_section(
            body,
            "Next actions",
            "1. Revise the Contract and this Goal in Discussion mode.\n"
            "2. Obtain approval before committing a new baseline.",
        )
    elif new_status in {"paused", "blocked"}:
        body = insert_section_bullet(
            body,
            "Progress and evidence",
            f"Goal {new_status}: {clean_reason}",
        )
        body = replace_markdown_section(body, "Blockers", f"- {clean_reason}")
        body = replace_markdown_section(
            body,
            "Next actions",
            f"1. Resolve or discuss: {clean_reason}\n"
            "2. Resume only after the recorded condition is addressed.",
        )
    elif new_status == "active":
        body = insert_section_bullet(
            body,
            "Progress and evidence",
            f"Goal resumed: {clean_reason or 'recorded pause or blocker resolved'}",
        )
        body = replace_markdown_section(body, "Blockers", "- None known.")
        body = replace_markdown_section(
            body,
            "Next actions",
            "1. Continue execution against the approved acceptance criteria.\n"
            "2. Verify deliverables before completing the Goal.",
        )
    metadata["status"] = new_status.upper()
    metadata["goal_revision"] = str(positive_int(metadata.get("goal_revision")) + 1)
    metadata["updated_at"] = now()
    atomic_write(GOAL_PATH, serialize_front_matter(metadata, body))
    return original, metadata


def _leave_goal_impl(
    goal_status: str, transition: str, reason: str, updated_by: str
) -> dict[str, Any]:
    if goal_status not in {
        "paused",
        "blocked",
        "completed",
        "needs_revision",
    }:
        raise ProjectStateError(f"Unsupported terminal goal status: {goal_status}")
    state = load_state()
    if state.get("mode") != "goal" or state.get("goal_status") != "active":
        raise ProjectStateError("There is no active Goal-mode run to transition.")

    previous_state = dict(state)
    original_goal = GOAL_PATH.read_text(encoding="utf-8")
    original_handoff = HANDOFF_PATH.read_text(encoding="utf-8")
    try:
        clean_reason = " ".join(reason.split()).strip()
        if not clean_reason:
            raise ProjectStateError("A non-empty transition reason is required.")
        if goal_status != "needs_revision":
            contract_meta, _, contract_text = load_markdown(CONTRACT_PATH)
            goal_meta_before, _, goal_text_before = load_markdown(GOAL_PATH)
            require_document_binding(
                state,
                label="PROJECT_CONTRACT.md",
                document_revision=positive_int(
                    contract_meta.get("contract_revision")
                ),
                document_text=contract_text,
                state_revision_key="contract_revision",
                state_fingerprint_key="contract_fingerprint",
            )
            require_document_binding(
                state,
                label="ACTIVE_GOAL.md",
                document_revision=positive_int(
                    goal_meta_before.get("goal_revision")
                ),
                document_text=goal_text_before,
                state_revision_key="active_goal_revision",
                state_fingerprint_key="active_goal_fingerprint",
            )
        _, goal_meta = set_goal_markdown_status(goal_status, clean_reason)
        sync_handoff_for_exit(goal_status, clean_reason)
        state.update(
            {
                "mode": "discussion",
                "goal_status": goal_status,
                "active_goal_revision": positive_int(goal_meta["goal_revision"]),
                "active_goal_fingerprint": file_fingerprint(GOAL_PATH),
                "last_transition": transition,
                "pause_reason": clean_reason,
                "updated_at": now(),
            }
        )
        write_state(state)
        return update_handoff_metadata(state, updated_by, status=goal_status)
    except Exception:
        atomic_write(GOAL_PATH, original_goal)
        atomic_write(HANDOFF_PATH, original_handoff)
        write_state(previous_state)
        raise


def leave_goal(
    goal_status: str, transition: str, reason: str, updated_by: str
) -> dict[str, Any]:
    with lifecycle_transaction(
        f"leave_goal:{goal_status}",
        (STATE_PATH, GOAL_PATH, HANDOFF_PATH),
    ):
        return _leave_goal_impl(
            goal_status, transition, reason, updated_by
        )


def _resume_goal_impl(updated_by: str) -> dict[str, Any]:
    state = load_state()
    if state.get("mode") != "discussion" or state.get("goal_status") not in {
        "paused",
        "blocked",
    }:
        raise ProjectStateError("Only a paused or blocked goal can be resumed.")
    contract_meta, goal_meta = validate_transition_documents_for_resume()
    original_goal = GOAL_PATH.read_text(encoding="utf-8")
    original_handoff = HANDOFF_PATH.read_text(encoding="utf-8")
    previous_state = dict(state)
    try:
        _, _, contract_text = load_markdown(CONTRACT_PATH)
        _, _, goal_text = load_markdown(GOAL_PATH)
        contract_fingerprint = require_document_binding(
            state,
            label="PROJECT_CONTRACT.md",
            document_revision=positive_int(contract_meta.get("contract_revision")),
            document_text=contract_text,
            state_revision_key="contract_revision",
            state_fingerprint_key="contract_fingerprint",
        )
        require_document_binding(
            state,
            label="ACTIVE_GOAL.md",
            document_revision=positive_int(goal_meta.get("goal_revision")),
            document_text=goal_text,
            state_revision_key="active_goal_revision",
            state_fingerprint_key="active_goal_fingerprint",
        )
        _, new_goal_meta = set_goal_markdown_status(
            "active", "Recorded pause or blocker resolved."
        )
        sync_handoff_for_goal(goal_meta["goal_id"].strip())
        state.update(
            {
                "mode": "goal",
                "goal_status": "active",
                "active_goal_id": goal_meta["goal_id"].strip(),
                "contract_revision": positive_int(
                    contract_meta["contract_revision"]
                ),
                "contract_fingerprint": contract_fingerprint,
                "active_goal_revision": positive_int(
                    new_goal_meta["goal_revision"]
                ),
                "active_goal_fingerprint": file_fingerprint(GOAL_PATH),
                "last_transition": "goal_resumed",
                "pause_reason": None,
                "updated_at": now(),
            }
        )
        write_state(state)
        return update_handoff_metadata(state, updated_by, status="active")
    except Exception:
        atomic_write(GOAL_PATH, original_goal)
        atomic_write(HANDOFF_PATH, original_handoff)
        write_state(previous_state)
        raise


def resume_goal(updated_by: str) -> dict[str, Any]:
    with lifecycle_transaction(
        "resume_goal", (STATE_PATH, GOAL_PATH, HANDOFF_PATH)
    ):
        return _resume_goal_impl(updated_by)


def validate_transition_documents_for_resume() -> tuple[dict[str, str], dict[str, str]]:
    contract_meta, _, contract_text = load_markdown(CONTRACT_PATH)
    goal_meta, _, goal_text = load_markdown(GOAL_PATH)
    problems: list[str] = []
    if normalized_scalar(contract_meta.get("status")) != "baselined":
        problems.append("PROJECT_CONTRACT.md status must remain BASELINED.")
    if positive_int(contract_meta.get("contract_revision")) < 1:
        problems.append("PROJECT_CONTRACT.md contract_revision must be at least 1.")
    if normalized_scalar(contract_meta.get("approved_by")) in {"", "null", "none"}:
        problems.append("PROJECT_CONTRACT.md approved_by must name the approver.")
    if normalized_scalar(goal_meta.get("status")) not in {"paused", "blocked"}:
        problems.append("ACTIVE_GOAL.md must be PAUSED or BLOCKED before resume.")
    if normalized_scalar(goal_meta.get("goal_id")) in {"", "none", "null"}:
        problems.append("ACTIVE_GOAL.md goal_id must remain set.")
    if positive_int(goal_meta.get("goal_revision")) < 1:
        problems.append("ACTIVE_GOAL.md goal_revision must be at least 1.")
    if critical_placeholders(contract_text) or critical_placeholders(goal_text):
        problems.append("Contract or Goal contains unresolved placeholders.")
    if problems:
        raise ProjectStateError("\n".join(problems))
    return contract_meta, goal_meta


def insert_section_bullet(text: str, heading: str, bullet: str) -> str:
    marker = f"## {heading}\n"
    start = text.find(marker)
    if start < 0:
        raise ProjectStateError(f"Missing section: {heading}")
    content_start = start + len(marker)
    next_heading = text.find("\n## ", content_start)
    if next_heading < 0:
        next_heading = len(text)
    section = text[content_start:next_heading]
    timestamped = f"\n- {now()} — {bullet.strip()}\n"
    return text[:content_start] + section.rstrip() + timestamped + text[next_heading:]


def _record_steering_impl(text: str, updated_by: str) -> dict[str, Any]:
    state = load_state()
    if state.get("mode") != "goal" or state.get("goal_status") != "active":
        raise ProjectStateError("Durable steering can be recorded only in active Goal mode.")
    clean_text = " ".join(text.split()).strip()
    if not clean_text:
        raise ProjectStateError("Durable steering text cannot be empty.")
    metadata, body, original = load_markdown(GOAL_PATH)
    contract_meta, _, contract_text = load_markdown(CONTRACT_PATH)
    problems: list[str] = []
    if normalized_scalar(contract_meta.get("status")) != "baselined":
        problems.append("PROJECT_CONTRACT.md status must remain BASELINED.")
    if positive_int(contract_meta.get("contract_revision")) < 1:
        problems.append("PROJECT_CONTRACT.md contract_revision must be at least 1.")
    if normalized_scalar(contract_meta.get("approved_by")) in {"", "null", "none"}:
        problems.append("PROJECT_CONTRACT.md approved_by must name the approver.")
    if critical_placeholders(contract_text):
        problems.append("PROJECT_CONTRACT.md contains unresolved placeholders.")
    if problems:
        raise ProjectStateError("\n".join(problems))

    contract_fingerprint = require_document_binding(
        state,
        label="PROJECT_CONTRACT.md",
        document_revision=positive_int(contract_meta.get("contract_revision")),
        document_text=contract_text,
        state_revision_key="contract_revision",
        state_fingerprint_key="contract_fingerprint",
        allow_revision_increment=True,
    )
    require_document_binding(
        state,
        label="ACTIVE_GOAL.md",
        document_revision=positive_int(metadata.get("goal_revision")),
        document_text=original,
        state_revision_key="active_goal_revision",
        state_fingerprint_key="active_goal_fingerprint",
    )

    new_body = insert_section_bullet(body, "Durable steering", clean_text)
    metadata["goal_revision"] = str(positive_int(metadata.get("goal_revision")) + 1)
    metadata["updated_at"] = now()
    new_goal_text = serialize_front_matter(metadata, new_body)
    previous_state = dict(state)
    state["active_goal_revision"] = positive_int(metadata["goal_revision"])
    state["active_goal_fingerprint"] = document_fingerprint(new_goal_text)
    state["contract_revision"] = positive_int(
        contract_meta.get("contract_revision")
    )
    state["contract_fingerprint"] = contract_fingerprint
    state["last_transition"] = "steering_recorded"
    try:
        atomic_write(GOAL_PATH, new_goal_text)
        write_state(state)
        return update_handoff_metadata(state, updated_by, status="active")
    except Exception:
        atomic_write(GOAL_PATH, original)
        write_state(previous_state)
        raise


def record_steering(text: str, updated_by: str) -> dict[str, Any]:
    with lifecycle_transaction(
        "record_steering", (STATE_PATH, GOAL_PATH, HANDOFF_PATH)
    ):
        return _record_steering_impl(text, updated_by)


def freshness(state: dict[str, Any]) -> tuple[bool, list[str]]:
    metadata, _, _ = load_markdown(HANDOFF_PATH)
    base, workspace, context = current_fingerprints()
    problems: list[str] = []
    if metadata.get("base_revision") != base:
        problems.append("base revision differs")
    if metadata.get("workspace_fingerprint") != workspace:
        problems.append("workspace fingerprint differs")
    if metadata.get("context_fingerprint") != context:
        problems.append("context fingerprint differs")
    if positive_int(metadata.get("handoff_revision")) != positive_int(
        state.get("handoff_revision")
    ):
        problems.append("handoff revision differs")
    return not problems, problems


def doctor(run_deep_check: bool = True) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for relative in REQUIRED_FILES:
        if not (ROOT / relative).is_file():
            errors.append(f"Missing required file: {relative}")
    if errors:
        return errors, warnings

    try:
        state = load_state()
    except ProjectStateError as exc:
        return [str(exc)], warnings

    mode = state.get("mode")
    goal_status = state.get("goal_status")
    if mode not in VALID_MODES:
        errors.append(f"Invalid mode: {mode}")
    if goal_status not in VALID_GOAL_STATUSES:
        errors.append(f"Invalid goal_status: {goal_status}")
    if mode == "goal" and goal_status != "active":
        errors.append("Goal mode requires goal_status=active.")
    if mode == "discussion" and goal_status == "active":
        errors.append("An active goal requires mode=goal.")

    try:
        handoff_meta, _, _ = load_markdown(HANDOFF_PATH)
        if normalized_scalar(handoff_meta.get("mode")) != str(mode):
            errors.append("HANDOFF mode does not match AI_PROJECT.json.")
        if normalized_scalar(handoff_meta.get("goal_status")) != str(goal_status):
            errors.append("HANDOFF goal_status does not match AI_PROJECT.json.")
        fresh, stale_reasons = freshness(state)
        if not fresh:
            if "handoff revision differs" in stale_reasons:
                errors.append(
                    "HANDOFF.md revision does not match AI_PROJECT.json."
                )
            remaining = [
                reason
                for reason in stale_reasons
                if reason != "handoff revision differs"
            ]
            if remaining:
                warnings.append("Handoff may be stale: " + ", ".join(remaining))
    except (OSError, ProjectStateError) as exc:
        errors.append(f"Cannot validate HANDOFF.md: {exc}")

    contract_meta, _, contract_text = load_markdown(CONTRACT_PATH)
    context_text = (ROOT / "PROJECT_CONTEXT.md").read_text(encoding="utf-8")
    goal_meta, _, goal_text = load_markdown(GOAL_PATH)
    if positive_int(state.get("contract_revision")) != positive_int(
        contract_meta.get("contract_revision")
    ):
        errors.append(
            "AI_PROJECT contract_revision does not match PROJECT_CONTRACT.md."
        )
    if positive_int(state.get("active_goal_revision")) != positive_int(
        goal_meta.get("goal_revision")
    ):
        errors.append(
            "AI_PROJECT active_goal_revision does not match ACTIVE_GOAL.md."
        )
    if positive_int(state.get("contract_revision")) > 0:
        expected_contract_fingerprint = str(
            state.get("contract_fingerprint") or ""
        )
        if not expected_contract_fingerprint:
            errors.append("AI_PROJECT has no bound Contract fingerprint.")
        elif expected_contract_fingerprint != document_fingerprint(contract_text):
            errors.append(
                "PROJECT_CONTRACT.md content differs from its bound revision."
            )
    if positive_int(state.get("active_goal_revision")) > 0:
        expected_goal_fingerprint = str(
            state.get("active_goal_fingerprint") or ""
        )
        if not expected_goal_fingerprint:
            errors.append("AI_PROJECT has no bound Active Goal fingerprint.")
        elif expected_goal_fingerprint != document_fingerprint(goal_text):
            errors.append(
                "ACTIVE_GOAL.md content differs from its bound revision."
            )
    if mode == "goal":
        try:
            validate_transition_documents()
        except ProjectStateError as exc:
            errors.extend(str(exc).splitlines())
    else:
        if critical_placeholders(contract_text):
            warnings.append("Project Contract is still a draft or contains unknown fields.")
        if critical_placeholders(context_text):
            warnings.append("Project Context still contains unknown fields.")
        if critical_placeholders(goal_text):
            warnings.append("No fully defined Active Goal is present.")

    if HANDOFF_PATH.stat().st_size > 16 * 1024:
        warnings.append("HANDOFF.md exceeds 16 KiB; move durable detail elsewhere.")

    if run_deep_check:
        result = subprocess.run(
            [sys.executable, str(MEMORY_TOOL), "check", str(ROOT)],
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            errors.append("Deep project-memory check failed.")
            detail = (result.stdout + "\n" + result.stderr).strip()
            if detail:
                errors.append(detail)
        else:
            for line in result.stdout.splitlines():
                if line.startswith("WARN"):
                    warnings.append(
                        "Deep project-memory check: "
                        + line.removeprefix("WARN").strip()
                    )
    return errors, warnings


def export_bundle(output: str | None) -> Path:
    errors, _ = doctor(run_deep_check=True)
    if errors:
        raise ProjectStateError("Export refused:\n" + "\n".join(errors))
    state = load_state()
    fresh, stale_reasons = freshness(state)
    if not fresh:
        raise ProjectStateError(
            "Export refused because HANDOFF.md is stale: "
            + ", ".join(stale_reasons)
        )
    command = [sys.executable, str(MEMORY_TOOL), "export", str(ROOT)]
    if output:
        command.extend(["--output", output])
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise ProjectStateError(
            "Export failed:\n" + (result.stdout + "\n" + result.stderr).strip()
        )
    for line in reversed(result.stdout.splitlines()):
        candidate = line.strip()
        if candidate.startswith("EXPORT  "):
            path = Path(candidate.removeprefix("EXPORT  ").strip())
            if path.is_file():
                return path
    raise ProjectStateError("Export succeeded but did not report the bundle path.")


def print_state(state: dict[str, Any]) -> None:
    fresh, reasons = freshness(state)
    payload = dict(state)
    payload["handoff_fresh"] = fresh
    payload["handoff_stale_reasons"] = reasons
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage Discussion/Goal state and portable checkpoints."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show machine state and Handoff freshness.")

    doctor_parser = sub.add_parser("doctor", help="Validate lifecycle and memory.")
    doctor_parser.add_argument(
        "--shallow", action="store_true", help="Skip the deeper memory audit."
    )

    checkpoint_parser = sub.add_parser(
        "checkpoint", help="Refresh revisions and fingerprints after semantic edits."
    )
    checkpoint_parser.add_argument("--updated-by", default="agent")
    checkpoint_parser.add_argument("--status")

    commit_parser = sub.add_parser(
        "commit-discussion",
        help="Validate the discussion baseline and enter active Goal mode.",
    )
    commit_parser.add_argument("--updated-by", default="agent")

    steer_parser = sub.add_parser(
        "steer", help="Record material steering and remain in active Goal mode."
    )
    steer_parser.add_argument("text")
    steer_parser.add_argument("--updated-by", default="agent")

    for name, help_text in (
        ("pause", "Explicitly pause and return to Discussion mode."),
        ("block", "Record a genuine blocker and return to Discussion mode."),
        ("invalidate", "Record Contract invalidation and return to Discussion mode."),
        ("complete", "Complete the Goal and return to Discussion mode."),
    ):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--reason", required=True)
        item.add_argument("--updated-by", default="agent")

    resume_parser = sub.add_parser("resume", help="Resume a paused or blocked Goal.")
    resume_parser.add_argument("--updated-by", default="agent")

    export_parser = sub.add_parser("export", help="Create a chat/API context bundle.")
    export_parser.add_argument("--output")
    return parser


def dispatch_command(args: argparse.Namespace) -> int:
    if args.command != "export":
        recover_lifecycle_transaction()
    if args.command == "status":
        print_state(load_state())
    elif args.command == "doctor":
        errors, warnings = doctor(run_deep_check=not args.shallow)
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}")
        if errors:
            return 1
        print("PASS: project lifecycle and memory are valid.")
    elif args.command == "checkpoint":
        print_state(checkpoint(args.updated_by, args.status))
    elif args.command == "commit-discussion":
        print_state(commit_discussion(args.updated_by))
    elif args.command == "steer":
        print_state(record_steering(args.text, args.updated_by))
    elif args.command == "pause":
        print_state(leave_goal("paused", "user_paused", args.reason, args.updated_by))
    elif args.command == "block":
        print_state(leave_goal("blocked", "goal_blocked", args.reason, args.updated_by))
    elif args.command == "invalidate":
        print_state(
            leave_goal(
                "needs_revision",
                "contract_invalidated",
                args.reason,
                args.updated_by,
            )
        )
    elif args.command == "complete":
        print_state(
            leave_goal("completed", "goal_completed", args.reason, args.updated_by)
        )
    elif args.command == "resume":
        print_state(resume_goal(args.updated_by))
    elif args.command == "export":
        print(export_bundle(args.output))
    else:
        raise ProjectStateError(f"Unsupported command: {args.command}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "export":
            return dispatch_command(args)
        with project_lock():
            return dispatch_command(args)
    except (OSError, ProjectStateError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
