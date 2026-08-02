---
name: ai-project-factory
description: Create, initialize, resume, validate, checkpoint, compact, export, or migrate a model-neutral AI project with Discussion and Goal modes. Use when the user asks to create an AI Project, start a portable project, continue work after context compaction, switch between Codex and Claude Code, prepare a Handoff, or export context for a file-only or API model.
---

# AI Project Factory

Use the shared Factory Core through `scripts/factory_bridge.py`. The Skill is a
thin adapter; never duplicate templates or lifecycle logic inside it.

Resolve the bridge before invoking it. In Claude Code, use
`${CLAUDE_SKILL_DIR}/scripts/factory_bridge.py`. In Codex, take the absolute
directory of the currently loaded `SKILL.md` supplied in the Skill metadata and
append `scripts/factory_bridge.py`. Never emit or execute an unresolved path
placeholder. The installed bridge is local-machine integration, not a
cloud/Cowork Skill.

## Create a project

If the user wants a visual flow or has not supplied a destination, run:

```bash
python "${CLAUDE_SKILL_DIR}/scripts/factory_bridge.py" gui
```

The shown command is the Claude Code spelling. In Codex, invoke the same bridge
using the resolved absolute path described above. For an explicit name and
parent directory, append:

```text
create --parent <path> --name <name> --profile <general|software|research>
```

Report the created path and begin the kickoff discussion in that project. Do
not invent a Contract or technical stack during creation.

## Run the kickoff discussion

Establish, in order:

1. desired outcome and why it matters;
2. concrete deliverables and acceptance evidence;
3. hard constraints, non-goals, and available artifacts;
4. unknowns that require inspection or a reversible experiment;
5. autonomy and the few conditions that require the user.

Push back on contradictions and suggest better routes. Keep exploration
reversible. Do not baseline the Contract until the user clearly approves
starting Goal execution.

## Work inside a Factory project

Read `AI_START_HERE.md` and follow its mode-specific routing.

- In Discussion mode, inspect, compare, prototype reversibly, and push back.
- After clear user approval, update Contract, Context, Decisions, and Active
  Goal; then run `.ai/project_runtime.py commit-discussion`.
- In Goal mode, treat ordinary new messages as steering and continue.
- If steering changes future work materially, record only the durable delta
  with `.ai/project_runtime.py steer`. When it amends a deliverable, hard
  constraint, or acceptance criterion without invalidating the Contract,
  increment the Contract revision first.
- Use `pause`, `invalidate`, `block`, or `complete` only for their defined
  lifecycle events.

## Compact, takeover, and migration

Before an explicit compact or provider switch, first update the semantic
sections of `HANDOFF.md`, then run:

```text
python .ai/project_runtime.py checkpoint --updated-by <agent-name>
python .ai/project_runtime.py doctor
```

For a local Agent, hand over the project directory and route it to
`AI_START_HERE.md`. For a chat/API-only model, run:

```text
python .ai/project_runtime.py export
```

Never claim that automatic compaction hooks ran unless the host supplied
evidence. Never put credentials, authenticated sessions, or private reasoning
into project memory or bundles.
