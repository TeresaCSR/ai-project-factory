#!/usr/bin/env python3
"""Initialize, validate, fingerprint, and export Portable Project Memory v1."""

from __future__ import annotations

import argparse
import hashlib
import sys

# This module imports the project lifecycle runtime during export. Disable bytecode
# before that loader can run so a read-only export never creates .ai/__pycache__.
sys.dont_write_bytecode = True

import importlib.util
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


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
)

ADAPTER_FILES = ("AGENTS.md", "CLAUDE.md")
PROJECT_TOOL = ".ai/project_memory.py"
PROJECT_RUNTIME = ".ai/project_runtime.py"
PROJECT_JOURNAL = ".ai/lifecycle_transaction.json"
PROJECT_LOCK = ".ai/lifecycle.lock"
PROJECT_RUNTIME_TEMP = ".ai/runtime-tmp"

REQUIRED_HANDOFF_HEADINGS = (
    "## Current objective",
    "## Confirmed state",
    "## Changed artifacts",
    "## Verification evidence",
    "## Decisions referenced",
    "## Risks and unknowns",
    "## Next actions",
    "## User decisions required",
)

REQUIRED_CONTEXT_HEADINGS = (
    "## Stable background",
    "## Project map",
    "## Commands and verification",
    "## Data and provenance",
    "## Runtime and capability dependencies",
    "## Glossary",
)

EXPORT_FILES = (
    "MIGRATION_PROMPT.md",
    "AI_START_HERE.md",
    "CONSTITUTION.md",
    "PROJECT_CONTRACT.md",
    "PROJECT_CONTEXT.md",
    "ACTIVE_GOAL.md",
    "AI_PROJECT.json",
    "HANDOFF.md",
    "DECISIONS.md",
    "ARTIFACTS.md",
)

MEMORY_FILENAMES = set(REQUIRED_FILES) | set(ADAPTER_FILES)
IGNORED_FINGERPRINT_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "node_modules",
    "__pycache__",
}

SECRET_PATTERNS = (
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    (
        "credential environment assignment",
        re.compile(
            r"(?im)\b[A-Z][A-Z0-9_]*(?:"
            r"SECRET_ACCESS_KEY|ACCESS_KEY_ID|API_KEY|TOKEN|SECRET|PASSWORD|PRIVATE_KEY"
            r")\b"
            r"\s*[:=]\s*[\"']?[^\s\"'#]{8,}"
        ),
    ),
    (
        "assigned credential",
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)\b"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{12,}"
        ),
    ),
    (
        "private key block",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"),
    ),
    (
        "credential in URL",
        re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]{6,}@"),
    ),
)

ABSOLUTE_PERSONAL_PATH_PATTERNS = (
    re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+"),
    re.compile(r"(?<!\w)/(?:home|Users)/[^/\s]+"),
)


def hidden_subprocess_kwargs() -> dict[str, object]:
    """Give nested helpers a console that is hidden from its first frame."""

    if os.name != "nt":
        return {}
    startup_type = getattr(subprocess, "STARTUPINFO", None)
    startup = startup_type() if startup_type is not None else None
    if startup is not None:
        startup.dwFlags |= int(
            getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        )
        startup.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
    return {
        "creationflags": int(
            getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
        ),
        "startupinfo": startup,
    }


def timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def timestamp_for_filename() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def skill_root() -> Path:
    return Path(__file__).resolve().parent.parent


def template_root() -> Path:
    return skill_root() / "assets" / "starter"


def resolve_project(raw_path: str) -> Path:
    project = Path(raw_path).expanduser().resolve()
    if not project.is_dir():
        raise ValueError(f"Project directory does not exist: {project}")
    return project


def render_template(text: str, project_name: str) -> str:
    safe_project_name = project_name.replace('"', '\\"')
    return (
        text.replace("{{PROJECT_NAME}}", safe_project_name)
        .replace("{{TIMESTAMP}}", timestamp())
        .replace("\r\n", "\n")
    )


def adapter_is_wired(project: Path, relative: str, text: str) -> bool:
    if "AI_START_HERE.md" in text:
        return True
    if relative == "CLAUDE.md" and "@AGENTS.md" in text:
        agents_path = project / "AGENTS.md"
        return (
            agents_path.is_file()
            and "AI_START_HERE.md" in agents_path.read_text(encoding="utf-8")
        )
    return False


def command_init(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    templates = template_root()
    if not templates.is_dir():
        raise ValueError(f"Starter templates are missing: {templates}")

    project_name = args.project_name or project.name
    created = 0
    skipped = 0
    skipped_relatives: set[str] = set()

    for source in sorted(path for path in templates.rglob("*") if path.is_file()):
        relative = source.relative_to(templates)
        destination = project / relative
        if destination.exists():
            print(f"SKIP    {relative} (already exists)")
            skipped += 1
            skipped_relatives.add(relative.as_posix())
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        content = render_template(source.read_text(encoding="utf-8"), project_name)
        destination.write_text(content, encoding="utf-8", newline="\n")
        print(f"CREATE  {relative}")
        created += 1

    embedded_tool = project / PROJECT_TOOL
    if embedded_tool.exists():
        print(f"SKIP    {PROJECT_TOOL} (already exists)")
        skipped += 1
        skipped_relatives.add(PROJECT_TOOL)
    else:
        embedded_tool.parent.mkdir(parents=True, exist_ok=True)
        embedded_tool.write_bytes(Path(__file__).read_bytes())
        print(f"CREATE  {PROJECT_TOOL}")
        created += 1

    print(f"\nInitialized {project}")
    print(f"Created: {created}; skipped: {skipped}; overwritten: 0")
    manual_actions = []
    for relative in ADAPTER_FILES:
        path = project / relative
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            if not adapter_is_wired(project, relative, text):
                manual_actions.append(relative)
    for relative in manual_actions:
        print(
            f"MANUAL  {relative} exists but is not wired to AI_START_HERE.md; "
            "merge the portable entry-point instruction into it."
        )

    compatibility_checks = {
        "AI_START_HERE.md": "Portable Project Memory v1",
        "PROJECT_CONTEXT.md": "schema_version: portable-project-memory/v1",
        "HANDOFF.md": "schema_version: portable-project-memory/v1",
        "DECISIONS.md": "# Decision Log",
        "ARTIFACTS.md": "# Artifact Manifest",
        "MIGRATION_PROMPT.md": "# AI Takeover Prompt",
        PROJECT_TOOL: "Portable Project Memory v1",
    }
    for relative, marker in compatibility_checks.items():
        if relative not in skipped_relatives:
            continue
        text = (project / relative).read_text(encoding="utf-8")
        if marker not in text:
            print(
                f"MANUAL  Existing {relative} is not a Portable Project Memory v1 "
                "file; reconcile it manually."
            )
    print("Next: replace unknown fields with facts, then record a workspace fingerprint.")
    return 0


def parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    result: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip("\"'")
    return result


def probable_secrets(relative: str, text: str) -> list[str]:
    findings: list[str] = []
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            findings.append(f"{relative}: probable {label}")
    return findings


def personal_paths(relative: str, text: str) -> list[str]:
    findings: list[str] = []
    for pattern in ABSOLUTE_PERSONAL_PATH_PATTERNS:
        if pattern.search(text):
            findings.append(f"{relative}: contains a machine-specific personal path")
            break
    return findings


def is_memory_relative(relative: str, project_scope: str = ".") -> bool:
    normalized = relative.replace("\\", "/")
    scope = project_scope.strip("/")
    if scope and scope != ".":
        prefix = scope + "/"
        if not normalized.startswith(prefix):
            return False
        normalized = normalized[len(prefix) :]
    if "/" in normalized:
        return False
    return normalized in MEMORY_FILENAMES or normalized.startswith(
        "AI_CONTEXT_BUNDLE_"
    )


def is_ephemeral_relative(relative: str, project_scope: str = ".") -> bool:
    normalized = relative.replace("\\", "/")
    scope = project_scope.strip("/")
    if scope and scope != ".":
        prefix = scope + "/"
        if not normalized.startswith(prefix):
            return False
        normalized = normalized[len(prefix) :]
    parts = normalized.split("/")
    return (
        normalized in {PROJECT_JOURNAL, PROJECT_LOCK}
        or normalized == PROJECT_RUNTIME_TEMP
        or normalized.startswith(PROJECT_RUNTIME_TEMP + "/")
        or any(part in IGNORED_FINGERPRINT_DIRS for part in parts[:-1])
        or normalized.endswith((".pyc", ".pyo"))
    )


def hash_file(path: Path, digest: "hashlib._Hash") -> None:
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)


def run_git(project: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ("git", "-C", str(project), *arguments),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(message or f"Git command failed: {' '.join(arguments)}")
    return completed.stdout


def try_git_root(project: Path) -> Path | None:
    try:
        completed = subprocess.run(
            ("git", "-C", str(project), "rev-parse", "--show-toplevel"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.decode("utf-8", errors="strict").strip()).resolve()


def split_null_paths(raw: bytes) -> set[str]:
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in raw.split(b"\0")
        if item
    }


def git_fingerprint(project: Path, root: Path) -> tuple[str, str]:
    try:
        scope = project.relative_to(root).as_posix() or "."
    except ValueError:
        scope = "."
    pathspec = scope

    head_result = subprocess.run(
        ("git", "-C", str(root), "rev-parse", "--verify", "HEAD"),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if head_result.returncode == 0:
        head = head_result.stdout.decode("ascii").strip()
    else:
        head = "UNBORN"

    tracked = split_null_paths(run_git(root, "ls-files", "-z", "--", pathspec))

    untracked = split_null_paths(
        run_git(
            root,
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            pathspec,
        )
    )
    candidates = sorted(tracked | untracked)
    stage_entries: dict[str, list[tuple[str, str, str]]] = {}
    for record in run_git(
        root, "ls-files", "--stage", "-z", "--", pathspec
    ).split(b"\0"):
        if not record or b"\t" not in record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        fields = metadata.decode("ascii", errors="strict").split()
        if len(fields) != 3:
            continue
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        stage_entries.setdefault(relative, []).append(
            (fields[0], fields[1], fields[2])
        )

    digest = hashlib.sha256()
    digest.update(b"git-working-content-v1\0")

    for relative in candidates:
        if is_memory_relative(relative, scope) or is_ephemeral_relative(
            relative, scope
        ):
            continue
        path = root / relative
        digest.update(relative.replace("\\", "/").encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        indexed_versions = sorted(
            stage_entries.get(relative, []),
            key=lambda item: (item[2], item[0], item[1]),
        )
        for stage_entry in indexed_versions:
            digest.update(
                ("INDEX\0" + "\0".join(stage_entry) + "\0").encode("ascii")
            )
        if path.is_symlink():
            digest.update(b"SYMLINK\0")
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        elif path.is_file():
            digest.update(b"FILE\0")
            executable = bool(path.stat().st_mode & 0o111)
            digest.update(b"EXECUTABLE\0" if executable else b"NONEXECUTABLE\0")
            hash_file(path, digest)
        elif (
            any(item[0] == "160000" for item in indexed_versions)
            and path.is_dir()
        ):
            digest.update(b"GITLINK\0")
            head_result = subprocess.run(
                ("git", "-C", str(path), "rev-parse", "--verify", "HEAD"),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            if head_result.returncode == 0:
                digest.update(head_result.stdout.strip())
                status_result = subprocess.run(
                    (
                        "git",
                        "-C",
                        str(path),
                        "status",
                        "--porcelain=v2",
                        "-z",
                        "--untracked-files=all",
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    **hidden_subprocess_kwargs(),
                )
                if status_result.returncode == 0:
                    digest.update(b"\0SUBMODULE_STATUS\0")
                    digest.update(status_result.stdout)
            else:
                digest.update(b"UNAVAILABLE")
        elif not path.exists():
            digest.update(b"DELETED\0")
        else:
            digest.update(b"OTHER\0")
        digest.update(b"\0")

    digest.update(f"scope:{scope}".encode())
    return f"git:{head}", f"sha256:{digest.hexdigest()}"


def filesystem_manifest_fingerprint(project: Path) -> tuple[str, str]:
    digest = hashlib.sha256()
    file_count = 0

    for directory, dirnames, filenames in os.walk(project, followlinks=False):
        base = Path(directory)
        linked_directories: list[Path] = []
        retained_directories: list[str] = []
        for name in dirnames:
            path = base / name
            if path.is_symlink():
                linked_directories.append(path)
            elif name not in IGNORED_FINGERPRINT_DIRS:
                retained_directories.append(name)
        dirnames[:] = sorted(retained_directories)

        for path in sorted(linked_directories):
            relative = path.relative_to(project).as_posix()
            if (
                path.name in IGNORED_FINGERPRINT_DIRS
                or is_ephemeral_relative(relative)
            ):
                continue
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0SYMLINK\0")
            digest.update(
                os.readlink(path).encode("utf-8", errors="surrogateescape")
            )
            digest.update(b"\0")
            file_count += 1

        for filename in sorted(filenames):
            path = base / filename
            relative = path.relative_to(project).as_posix()
            if is_memory_relative(relative) or is_ephemeral_relative(relative):
                continue
            digest.update(relative.encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
            if path.is_symlink():
                digest.update(b"SYMLINK\0")
                digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
            elif path.is_file():
                digest.update(b"FILE\0")
                hash_file(path, digest)
            else:
                digest.update(b"OTHER\0")
            digest.update(b"\0")
            file_count += 1

    digest.update(f"file-count:{file_count}".encode())
    return "filesystem-content", f"sha256:{digest.hexdigest()}"


def compute_fingerprint(project: Path) -> tuple[str, str]:
    root = try_git_root(project)
    if root is not None:
        return git_fingerprint(project, root)
    return filesystem_manifest_fingerprint(project)


def compute_context_fingerprint(project: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"portable-project-context-v1\0")
    context_files = [
        relative for relative in REQUIRED_FILES if relative != "HANDOFF.md"
    ]
    context_files.extend(
        relative for relative in ADAPTER_FILES if (project / relative).is_file()
    )
    for relative in sorted(context_files):
        path = project / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            hash_file(path, digest)
        else:
            digest.update(b"MISSING")
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def command_fingerprint(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    base_revision, workspace_fingerprint = compute_fingerprint(project)
    print(f"base_revision: {base_revision}")
    print(f"workspace_fingerprint: {workspace_fingerprint}")
    print(f"context_fingerprint: {compute_context_fingerprint(project)}")
    return 0


def command_hash_file(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"File does not exist: {path}")
    display_path = path.name
    if args.project:
        project = resolve_project(args.project)
        try:
            display_path = path.relative_to(project).as_posix()
        except ValueError:
            display_path = f"<external>/{path.name}"
    print(f"path: {display_path}")
    print(f"size_bytes: {path.stat().st_size}")
    print(f"sha256: {file_sha256(path)}")
    return 0


def clean_table_cell(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped.startswith("`") and stripped.endswith("`"):
        return stripped[1:-1]
    return stripped


def verify_artifact_manifest(
    project: Path, text: str
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    allowed_availability = {"VERIFIED", "EXTERNAL", "MISSING", "UNKNOWN"}

    in_table = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped_line = line.strip()
        if stripped_line.startswith("| ID |"):
            in_table = True
            continue
        if not in_table:
            continue
        if not stripped_line:
            in_table = False
            continue
        if not stripped_line.startswith("|"):
            in_table = False
            continue
        if not stripped_line.endswith("|"):
            errors.append(
                f"ARTIFACTS.md line {line_number}: malformed artifact table row"
            )
            continue

        cells = [
            clean_table_cell(cell) for cell in stripped_line.strip("|").split("|")
        ]
        if len(cells) != 7:
            errors.append(
                f"ARTIFACTS.md line {line_number}: expected 7 columns, got {len(cells)}; "
                "do not use pipe characters inside cells"
            )
            continue
        artifact_id, location, _version, size_text, sha_text, availability, _notes = cells
        if artifact_id in {"---", "尚无"} or set(artifact_id) == {"-"}:
            continue

        availability = availability.upper()
        label = f"ARTIFACTS.md line {line_number} ({artifact_id})"
        if availability not in allowed_availability:
            errors.append(
                f"{label}: availability must be one of "
                + ", ".join(sorted(allowed_availability))
            )
            continue

        is_external = "://" in location or location.startswith("<external>/")
        if availability == "EXTERNAL":
            if not is_external:
                warnings.append(f"{label}: EXTERNAL entry has no URI/external marker")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", sha_text):
                warnings.append(f"{label}: external artifact has no valid SHA-256")
            continue

        if availability == "UNKNOWN":
            warnings.append(f"{label}: artifact availability is UNKNOWN")
            continue

        if is_external:
            errors.append(f"{label}: local availability state uses an external location")
            continue

        relative = Path(location)
        if relative.is_absolute():
            errors.append(f"{label}: artifact path must be project-relative")
            continue

        candidate = (project / relative).resolve()
        try:
            candidate.relative_to(project)
        except ValueError:
            errors.append(f"{label}: artifact path escapes the project root")
            continue

        if availability == "MISSING":
            if candidate.exists():
                warnings.append(f"{label}: marked MISSING but the path currently exists")
            continue

        if not candidate.is_file():
            errors.append(f"{label}: VERIFIED artifact is missing: {location}")
            continue

        try:
            expected_size = int(size_text)
        except ValueError:
            errors.append(f"{label}: size bytes is not an integer: {size_text}")
        else:
            actual_size = candidate.stat().st_size
            if expected_size != actual_size:
                errors.append(
                    f"{label}: size mismatch; recorded {expected_size}, actual {actual_size}"
                )

        if not re.fullmatch(r"[0-9a-fA-F]{64}", sha_text):
            errors.append(f"{label}: VERIFIED artifact has no valid SHA-256")
        else:
            actual_sha = file_sha256(candidate)
            if sha_text.lower() != actual_sha:
                errors.append(
                    f"{label}: SHA-256 mismatch; recorded {sha_text.lower()}, "
                    f"actual {actual_sha}"
                )

    return errors, warnings


def verify_decision_ids(text: str) -> list[str]:
    errors: list[str] = []
    ids: list[str] = []
    in_fence = False
    valid_pattern = re.compile(r"^D-\d{8}-\d{6}-[a-z0-9]{4,8}$")
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line.startswith("## D-"):
            continue
        decision_id = line[3:].split("—", 1)[0].strip()
        if not valid_pattern.fullmatch(decision_id):
            errors.append(f"DECISIONS.md has invalid decision ID: {decision_id}")
            continue
        ids.append(decision_id)

    seen: set[str] = set()
    for decision_id in ids:
        if decision_id in seen:
            errors.append(f"DECISIONS.md contains duplicate ID: {decision_id}")
        seen.add(decision_id)
    return errors


def git_portability_status(project: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    root = try_git_root(project)
    if root is None:
        return errors, warnings

    try:
        scope = project.relative_to(root).as_posix() or "."
    except ValueError:
        scope = "."

    portable_files = list(REQUIRED_FILES) + [PROJECT_TOOL, PROJECT_RUNTIME]
    portable_files.extend(
        relative for relative in ADAPTER_FILES if (project / relative).is_file()
    )
    ignored: list[str] = []
    untracked: list[str] = []
    modified: list[str] = []

    for relative in portable_files:
        root_relative = relative if scope == "." else f"{scope}/{relative}"
        tracked_result = subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "ls-files",
                "--error-unmatch",
                "--",
                root_relative,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        if tracked_result.returncode == 0:
            worktree_diff = subprocess.run(
                ("git", "-C", str(root), "diff", "--quiet", "--", root_relative),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            staged_diff = subprocess.run(
                (
                    "git",
                    "-C",
                    str(root),
                    "diff",
                    "--cached",
                    "--quiet",
                    "--",
                    root_relative,
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                **hidden_subprocess_kwargs(),
            )
            if worktree_diff.returncode == 1 or staged_diff.returncode == 1:
                modified.append(relative)
            continue

        ignored_result = subprocess.run(
            ("git", "-C", str(root), "check-ignore", "-q", "--", root_relative),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        if ignored_result.returncode == 0:
            ignored.append(relative)
        else:
            untracked.append(relative)

    if ignored:
        errors.append(
            "Portable project-memory files are Git-ignored and will be missing "
            "after clone: " + ", ".join(sorted(ignored))
        )
    if untracked:
        warnings.append(
            "Portable project-memory files are untracked and will be missing "
            "after clone unless added to Git or transferred with the full directory: "
            + ", ".join(sorted(untracked))
        )
    if modified:
        warnings.append(
            "Portable project-memory files have uncommitted changes and a clone "
            "will receive an older version: " + ", ".join(sorted(modified))
        )
    return errors, warnings


def inspect_project(project: Path) -> tuple[list[str], list[str], dict[str, str]]:
    errors: list[str] = []
    warnings: list[str] = []
    loaded: dict[str, str] = {}
    journal_path = project / PROJECT_JOURNAL
    journal_seen = journal_path.exists()

    for relative in REQUIRED_FILES:
        path = project / relative
        if not path.is_file():
            errors.append(f"Missing required file: {relative}")
            continue
        loaded[relative] = path.read_text(encoding="utf-8")

    required_markers = {
        "AI_START_HERE.md": (
            "# AI Project Entry Point",
            "## Read only what the current mode needs",
            "## Discussion mode",
            "## Goal mode",
            "## Handoff and compact",
        ),
        "MIGRATION_PROMPT.md": ("# AI Takeover Prompt",),
        "DECISIONS.md": ("# Decision Log", "# Decisions"),
        "ARTIFACTS.md": ("# Artifact Manifest", "Availability"),
    }
    for relative, markers in required_markers.items():
        text = loaded.get(relative, "")
        for marker in markers:
            if marker not in text:
                errors.append(f"{relative} missing required marker: {marker}")

    project_tool = project / PROJECT_TOOL
    if not project_tool.is_file():
        errors.append(f"Missing portable project tool: {PROJECT_TOOL}")
    elif "Portable Project Memory v1" not in project_tool.read_text(encoding="utf-8"):
        errors.append(f"{PROJECT_TOOL} is not a compatible Portable Project Memory v1 tool")

    for relative in ADAPTER_FILES:
        path = project / relative
        if not path.is_file():
            continue
        adapter = path.read_text(encoding="utf-8")
        loaded[relative] = adapter
        if not adapter_is_wired(project, relative, adapter):
            warnings.append(f"{relative} does not point to AI_START_HERE.md")

    context = loaded.get("PROJECT_CONTEXT.md", "")
    for heading in REQUIRED_CONTEXT_HEADINGS:
        if heading not in context:
            errors.append(f"PROJECT_CONTEXT.md missing heading: {heading}")

    handoff = loaded.get("HANDOFF.md", "")
    for heading in REQUIRED_HANDOFF_HEADINGS:
        if heading not in handoff:
            errors.append(f"HANDOFF.md missing heading: {heading}")

    metadata = parse_frontmatter(handoff)
    required_metadata = (
        "schema_version",
        "handoff_revision",
        "updated_at",
        "updated_by",
        "base_revision",
        "workspace_fingerprint",
        "context_fingerprint",
        "status",
    )
    for key in required_metadata:
        if not metadata.get(key):
            errors.append(f"HANDOFF.md missing frontmatter key: {key}")

    if metadata.get("schema_version") not in (None, "portable-project-memory/v1"):
        errors.append(
            "HANDOFF.md has unsupported schema_version: "
            f"{metadata.get('schema_version')}"
        )

    revision = metadata.get("handoff_revision")
    if revision:
        try:
            if int(revision) < 0:
                raise ValueError
        except ValueError:
            errors.append(f"HANDOFF.md handoff_revision is not a non-negative integer: {revision}")

    updated_at = metadata.get("updated_at")
    if updated_at:
        try:
            parsed_updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            if parsed_updated_at.utcoffset() is None:
                errors.append("HANDOFF.md updated_at must include a timezone offset")
        except ValueError:
            errors.append(f"HANDOFF.md updated_at is not ISO-8601: {updated_at}")

    allowed_statuses = {
        "not_started",
        "none",
        "active",
        "blocked",
        "paused",
        "completed",
        "needs_revision",
        "ready_for_compact",
    }
    status = metadata.get("status")
    if status and status not in allowed_statuses:
        errors.append(
            "HANDOFF.md status must be one of "
            + ", ".join(sorted(allowed_statuses))
            + f"; got {status}"
        )

    if "unverified-initial-template" in (
        metadata.get("base_revision"),
        metadata.get("workspace_fingerprint"),
        metadata.get("context_fingerprint"),
    ):
        warnings.append("HANDOFF.md is still based on the unverified initial template")
    elif not errors:
        _, current_fingerprint = compute_fingerprint(project)
        if metadata.get("workspace_fingerprint") != current_fingerprint:
            errors.append(
                "HANDOFF.md workspace_fingerprint is stale: "
                f"recorded {metadata.get('workspace_fingerprint')}, "
                f"current {current_fingerprint}"
            )
        current_context_fingerprint = compute_context_fingerprint(project)
        if metadata.get("context_fingerprint") != current_context_fingerprint:
            errors.append(
                "HANDOFF.md context_fingerprint is stale: "
                f"recorded {metadata.get('context_fingerprint')}, "
                f"current {current_context_fingerprint}"
            )

    if len(handoff.encode("utf-8")) > 16 * 1024:
        warnings.append("HANDOFF.md exceeds 16 KiB; move durable history elsewhere")

    if handoff and not any(
        token in handoff for token in ("PASS", "FAIL", "NOT_RUN", "UNKNOWN", "BLOCKED")
    ):
        warnings.append("HANDOFF.md verification table has no explicit result state")

    for relative, text in loaded.items():
        errors.extend(probable_secrets(relative, text))
        errors.extend(personal_paths(relative, text))
        if "{{" in text and "}}" in text:
            warnings.append(f"{relative} contains an unresolved template token")

    if "待填写" in context:
        warnings.append("PROJECT_CONTEXT.md still contains fields marked 待填写")

    artifact_errors, artifact_warnings = verify_artifact_manifest(
        project, loaded.get("ARTIFACTS.md", "")
    )
    errors.extend(artifact_errors)
    warnings.extend(artifact_warnings)
    errors.extend(verify_decision_ids(loaded.get("DECISIONS.md", "")))
    portability_errors, portability_warnings = git_portability_status(project)
    errors.extend(portability_errors)
    warnings.extend(portability_warnings)

    if journal_seen or journal_path.exists():
        errors.insert(
            0,
            "A lifecycle transaction is incomplete; run "
            ".ai/project_runtime.py status or doctor to recover it before "
            "trusting this check.",
        )

    return errors, warnings, loaded


def command_check(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    errors, warnings, _ = inspect_project(project)

    print(f"Portable Project Memory check: {project}")
    for item in errors:
        print(f"ERROR   {item}")
    for item in warnings:
        print(f"WARN    {item}")

    if not errors and not warnings:
        print("OK      No structural, freshness, or safety issues found")
    else:
        print(f"\nErrors: {len(errors)}; warnings: {len(warnings)}")

    return 1 if errors else 0


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    hash_file(path, digest)
    return digest.hexdigest()


def write_new_file_atomically(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp, path)
        except FileExistsError as exc:
            raise ValueError(f"Refusing to overwrite existing export: {path}") from exc
        except OSError as exc:
            if os.name != "nt":
                raise ValueError(
                    "Export filesystem does not support atomic no-overwrite publication."
                ) from exc
            try:
                os.rename(temp, path)
            except FileExistsError as exists_exc:
                raise ValueError(
                    f"Refusing to overwrite existing export: {path}"
                ) from exists_exc
    finally:
        temp.unlink(missing_ok=True)


def cleanup_project_runtime_temps(project: Path) -> None:
    ai_dir = (project / ".ai").resolve()
    temp_dir = project / PROJECT_RUNTIME_TEMP
    if not temp_dir.exists():
        return
    if (
        not temp_dir.is_dir()
        or temp_dir.is_symlink()
        or temp_dir.resolve().parent != ai_dir
    ):
        raise ValueError(
            ".ai/runtime-tmp must be a real directory inside this project."
        )
    for candidate in temp_dir.iterdir():
        if (
            candidate.name.startswith(".")
            and candidate.name.endswith(".tmp")
            and (candidate.is_file() or candidate.is_symlink())
        ):
            candidate.unlink()


@contextmanager
def lifecycle_lock(project: Path, timeout_seconds: float = 30.0):
    lock_path = project / PROJECT_LOCK
    lock_path.parent.mkdir(parents=True, exist_ok=True)
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

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise ValueError(
                        "Another project lifecycle command is still running."
                    )
                time.sleep(0.05)
        cleanup_project_runtime_temps(project)
        yield
    finally:
        if acquired:
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


def ensure_export_fresh(project: Path, handoff: str) -> None:
    metadata = parse_frontmatter(handoff)
    base = metadata.get("base_revision", "")
    fingerprint = metadata.get("workspace_fingerprint", "")
    context_fingerprint = metadata.get("context_fingerprint", "")
    if (
        not base
        or not fingerprint
        or not context_fingerprint
        or "unverified-initial-template"
        in (base, fingerprint, context_fingerprint)
    ):
        raise ValueError(
            "Export stopped because HANDOFF.md has no verified workspace/context "
            "fingerprint"
        )

    current_base, current_fingerprint = compute_fingerprint(project)
    current_context_fingerprint = compute_context_fingerprint(project)
    if (
        base != current_base
        or fingerprint != current_fingerprint
        or context_fingerprint != current_context_fingerprint
    ):
        raise ValueError(
            "Export stopped because HANDOFF.md is stale. Refresh its "
            "base_revision, workspace_fingerprint, and context_fingerprint "
            "after verifying the project."
        )


def factory_lifecycle_errors(project: Path) -> list[str]:
    runtime_path = project / PROJECT_RUNTIME
    if not runtime_path.is_file():
        return [f"Missing Factory lifecycle runtime: {PROJECT_RUNTIME}"]
    spec = importlib.util.spec_from_file_location(
        "_factory_export_lifecycle_validation", runtime_path
    )
    if spec is None or spec.loader is None:
        return [f"Cannot load Factory lifecycle runtime: {PROJECT_RUNTIME}"]
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        errors, _ = module.doctor(run_deep_check=False)
    except Exception as exc:
        return [f"Factory lifecycle validation failed to run: {exc}"]
    return [str(item) for item in errors]


def _command_export_locked(args: argparse.Namespace, project: Path) -> int:
    missing = [relative for relative in EXPORT_FILES if not (project / relative).is_file()]
    if missing:
        raise ValueError("Cannot export; missing: " + ", ".join(missing))

    loaded_bytes = {
        relative: (project / relative).read_bytes()
        for relative in EXPORT_FILES
    }
    try:
        loaded = {
            relative: content.decode("utf-8")
            for relative, content in loaded_bytes.items()
        }
    except UnicodeDecodeError as exc:
        raise ValueError(f"Cannot export non-UTF-8 project memory: {exc}") from exc
    structural_errors, _, _ = inspect_project(project)
    if structural_errors:
        details = "\n".join(f"- {item}" for item in structural_errors)
        raise ValueError(
            "Export stopped because the project memory check failed:\n" + details
        )
    lifecycle_errors = factory_lifecycle_errors(project)
    if lifecycle_errors:
        details = "\n".join(f"- {item}" for item in lifecycle_errors)
        raise ValueError(
            "Export stopped because the Factory lifecycle is invalid:\n" + details
        )
    ensure_export_fresh(project, loaded["HANDOFF.md"])

    secret_findings: list[str] = []
    personal_path_findings: list[str] = []
    for relative, text in loaded.items():
        secret_findings.extend(probable_secrets(relative, text))
        personal_path_findings.extend(personal_paths(relative, text))
    if secret_findings:
        details = "\n".join(f"- {item}" for item in secret_findings)
        raise ValueError(
            "Export stopped because probable credentials were detected:\n" + details
        )
    if personal_path_findings:
        details = "\n".join(f"- {item}" for item in personal_path_findings)
        raise ValueError(
            "Export stopped because machine-specific personal paths were detected:\n"
            + details
        )

    if args.output:
        output = Path(args.output).expanduser()
        if not output.is_absolute():
            output = Path.cwd() / output
        output = output.resolve()
    else:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", project.name).strip("-") or "project"
        output = (
            Path(tempfile.gettempdir())
            / "portable-ai-handoff-exports"
            / f"{safe_name}_AI_CONTEXT_BUNDLE_{timestamp_for_filename()}.md"
        )

    if output.exists():
        raise ValueError(f"Refusing to overwrite existing export: {output}")
    try:
        output.relative_to(project)
    except ValueError:
        pass
    else:
        raise ValueError(
            "Refusing to write an export inside the project because it would change "
            "the project state. Choose a path outside the project or use the temp default."
        )

    manifest = [
        f"| `{relative}` | `{hashlib.sha256(loaded_bytes[relative]).hexdigest()}` |"
        for relative in EXPORT_FILES
    ]

    parts = [
        "<!-- GENERATED FILE: do not treat this bundle as the editable source of truth. -->",
        "",
        "# Portable AI Context Bundle",
        "",
        f"- Generated at: `{timestamp()}`",
        f"- Project: `{project.name}`",
        "- Protocol: `portable-project-memory/v1`",
        "",
        "## Privacy boundary",
        "",
        "This bundle embeds only project-level allowlisted files. Validation may",
        "read thin adapters, the project helper, and hash VERIFIED local artifacts,",
        "but none of those additional contents are embedded. The bundle intentionally",
        "excludes user-level profiles, global AGENTS files, credentials, chats, and",
        "automatic memory stores. Secret scanning is best-effort; review before sharing.",
        "",
        "## Instructions for the receiving AI",
        "",
        "Use the embedded takeover prompt. Treat claims as last-known context,",
        "verify them against separately supplied artifacts, and label anything you",
        "cannot inspect. Return proposed changes as patches or complete replacement",
        "sections; do not claim to have modified local files unless your host actually",
        "provides filesystem access.",
        "",
        "## Manifest",
        "",
        "| File | SHA-256 |",
        "|---|---|",
        *manifest,
    ]

    for relative in EXPORT_FILES:
        text = loaded[relative].rstrip()
        parts.extend(
            (
                "",
                "---",
                "",
                f"<!-- BEGIN FILE: {relative} -->",
                "",
                text,
                "",
                f"<!-- END FILE: {relative} -->",
            )
        )

    changed = [
        relative
        for relative, snapshot in loaded_bytes.items()
        if (project / relative).read_bytes() != snapshot
    ]
    if changed:
        raise ValueError(
            "Export stopped because project memory changed during snapshot creation: "
            + ", ".join(changed)
        )
    final_errors, _, _ = inspect_project(project)
    if final_errors:
        details = "\n".join(f"- {item}" for item in final_errors)
        raise ValueError(
            "Export stopped because final project validation failed:\n" + details
        )
    ensure_export_fresh(project, loaded["HANDOFF.md"])

    bundle = ("\n".join(parts) + "\n").encode("utf-8")
    write_new_file_atomically(output, bundle)
    print(f"EXPORT  {output}")
    print("Included only the Portable Project Memory allowlist.")
    print("Secret detection is best-effort; review the bundle before sharing.")
    return 0


def command_export(args: argparse.Namespace) -> int:
    project = resolve_project(args.project)
    with lifecycle_lock(project):
        journal = project / PROJECT_JOURNAL
        if journal.exists():
            raise ValueError(
                "Export stopped because a lifecycle transaction is incomplete. "
                "Run .ai/project_runtime.py status or doctor to recover it first."
            )
        return _command_export_locked(args, project)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Initialize, check, fingerprint, or export Portable Project Memory v1."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="Create missing memory files without overwriting existing files."
    )
    init_parser.add_argument("project", help="Existing project directory.")
    init_parser.add_argument("--project-name", help="Project name written to the template.")
    init_parser.set_defaults(handler=command_init)

    check_parser = subparsers.add_parser(
        "check", help="Validate structure, freshness, adapters, and secret risks."
    )
    check_parser.add_argument("project", help="Project directory.")
    check_parser.set_defaults(handler=command_check)

    fingerprint_parser = subparsers.add_parser(
        "fingerprint",
        help="Print the base revision, workspace fingerprint, and context fingerprint.",
    )
    fingerprint_parser.add_argument("project", help="Project directory.")
    fingerprint_parser.set_defaults(handler=command_fingerprint)

    hash_parser = subparsers.add_parser(
        "hash-file", help="Print SHA-256 and size for an important binary artifact."
    )
    hash_parser.add_argument("path", help="File to hash.")
    hash_parser.add_argument(
        "--project",
        help="Project root used to print a portable relative path.",
    )
    hash_parser.set_defaults(handler=command_hash_file)

    export_parser = subparsers.add_parser(
        "export", help="Create a context bundle for an agent without filesystem access."
    )
    export_parser.add_argument("project", help="Project directory.")
    export_parser.add_argument(
        "--output",
        help="Output path. The default is a timestamped file under the OS temp directory.",
    )
    export_parser.set_defaults(handler=command_export)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"ERROR   {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
