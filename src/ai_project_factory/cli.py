"""Command-line interface used by adapters and automated tests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import (
    PROFILES,
    REPO_ROOT,
    CreateProjectRequest,
    FactoryError,
    checkpoint_project,
    create_project,
    doctor_project,
    export_project,
    inspect_project,
    sync_agent_skills,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Project Factory Demo")
    sub = parser.add_subparsers(dest="command", required=True)

    gui = sub.add_parser("gui", help="Open the one-click desktop GUI.")
    gui.add_argument("--smoke-test", action="store_true")

    create = sub.add_parser("create", help="Create a new portable AI project.")
    create.add_argument("--parent", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--directory-name")
    create.add_argument("--profile", choices=PROFILES, default="general")
    create.add_argument("--no-git", action="store_true")

    for name in ("status", "doctor", "checkpoint", "export"):
        item = sub.add_parser(name)
        item.add_argument("project")
        if name == "doctor":
            item.add_argument("--shallow", action="store_true")
        elif name == "checkpoint":
            item.add_argument("--updated-by", default="factory-cli")
            item.add_argument("--status")
        elif name == "export":
            item.add_argument("--output")

    sync = sub.add_parser(
        "sync-adapters", help="Install or refresh the same thin Skill source."
    )
    sync.add_argument("--codex-skills")
    sync.add_argument("--claude-skills")
    sync.add_argument("--factory-root", default=str(REPO_ROOT))
    return parser


def relay(result) -> int:
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(
            result.stderr,
            file=sys.stderr,
            end="" if result.stderr.endswith("\n") else "\n",
        )
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "gui":
            from .gui import launch_gui

            launch_gui(smoke_test=args.smoke_test)
            return 0
        if args.command == "create":
            result = create_project(
                CreateProjectRequest(
                    parent=Path(args.parent),
                    project_name=args.name,
                    profile=args.profile,
                    initialize_git=not args.no_git,
                    directory_name=args.directory_name,
                )
            )
            print(
                json.dumps(
                    {
                        "project_path": str(result.project_path),
                        "created_files": list(result.created_files),
                        "doctor": result.doctor_output,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        project = Path(getattr(args, "project", "."))
        if args.command == "status":
            return relay(inspect_project(project))
        if args.command == "doctor":
            return relay(doctor_project(project, deep=not args.shallow))
        if args.command == "checkpoint":
            return relay(
                checkpoint_project(project, args.updated_by, status=args.status)
            )
        if args.command == "export":
            output = Path(args.output) if args.output else None
            return relay(export_project(project, output))
        if args.command == "sync-adapters":
            roots: list[Path] = []
            if args.codex_skills:
                roots.append(Path(args.codex_skills))
            if args.claude_skills:
                roots.append(Path(args.claude_skills))
            destinations = sync_agent_skills(
                roots,
                Path(args.factory_root),
            )
            print("\n".join(str(destination) for destination in destinations))
            return 0
        raise FactoryError(f"不支持的命令：{args.command}")
    except FactoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
