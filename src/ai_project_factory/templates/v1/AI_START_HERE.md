# AI Project Entry Point

This project was created by AI Project Factory. The project files are the
source of truth; a chat window is disposable.

## Read only what the current mode needs

Always read:

1. `AI_PROJECT.json` for machine state and pinned versions.
2. `CONSTITUTION.md` for the small, project-wide operating principles.
3. `PROJECT_CONTRACT.md` for the approved outcome and boundaries.
4. `PROJECT_CONTEXT.md` for stable, verified background.

Then:

- In `discussion` mode, read relevant decisions and actual project artifacts.
- In `goal` mode, also read `ACTIVE_GOAL.md`, `HANDOFF.md`, relevant entries in
  `DECISIONS.md`, and the artifacts named by them.
- Read `ARTIFACTS.md` only when binary, ignored, or external artifacts matter.

Actual artifacts and reproducible evidence override prose memory. Mark missing
information `unknown`; never make up facts to complete a template.

## Discussion mode

Explore the problem, inspect evidence, push back on weak assumptions, compare
reasonable alternatives, and use small reversible prototypes when useful.
Do not silently turn a candidate idea into an approved project direction.

When the user clearly approves execution, materialize the discussion before
working:

1. stable facts -> `PROJECT_CONTEXT.md`;
2. outcome, scope, constraints, acceptance, and authorization ->
   `PROJECT_CONTRACT.md`;
3. durable choices and rejected alternatives -> `DECISIONS.md`;
4. the current bounded objective -> `ACTIVE_GOAL.md`;
5. run `.ai/project_runtime.py commit-discussion`.

The transition is valid only after all files pass validation.

## Goal mode

Continue autonomously until the active goal is complete. A normal user message
is steering: incorporate it and keep working. Do not ask whether to continue
after routine attempts, optimization, refactoring, test failure, dependency
changes, or a failed implementation path.

Pause only when:

1. a new instruction invalidates the approved outcome, core deliverable, hard
   constraint, project boundary, or acceptance criteria;
2. the next action crosses the recorded recovery boundary without prior
   authorization;
3. user-only capability is required, such as login, payment, a credential,
   physical action, or personal approval;
4. reasonable recovery paths are exhausted and meaningful progress is blocked;
5. the user explicitly says to pause or return to discussion.

Use `.ai/project_runtime.py pause`, `block`, or `complete` to record the state.
Completion returns the project to `discussion` mode and stops execution.

## Handoff and compact

`HANDOFF.md` is a short cold-start snapshot, not a transcript. Update its
meaningful sections after a material artifact, decision, verification result,
blocker, mode transition, milestone, completion, or before an explicit compact
or provider switch. Then run:

```text
python .ai/project_runtime.py checkpoint --updated-by <agent-name>
```

Do not update it for greetings, status-only questions, or unchanged discussion.
The deterministic runtime updates revisions and freshness fingerprints; the
Agent remains responsible for an honest semantic summary.

## Tools and migration

- `python .ai/project_runtime.py doctor` validates the Factory state.
- `python .ai/project_memory.py check .` performs the deeper memory audit.
- `python .ai/project_runtime.py export` creates a chat/API context bundle.
- Local agents should use the project directory directly.
- File-only or chat-only models must mark execution-dependent checks `NOT_RUN`.

Never put credentials, authenticated sessions, personal secrets, or private
chain-of-thought into project memory or exported bundles.
