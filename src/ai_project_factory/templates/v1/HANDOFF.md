---
schema_version: portable-project-memory/v1
factory_schema_version: ai-project-factory/handoff-v1
handoff_revision: 0
updated_at: "{{TIMESTAMP}}"
updated_by: initializer
base_revision: unverified-initial-template
workspace_fingerprint: unverified-initial-template
context_fingerprint: unverified-initial-template
status: not_started
mode: discussion
goal_status: none
active_goal_id: none
---

# Project Handoff

This is the current cold-start snapshot. Keep it concise and replace stale
prose; do not append chat history.

## Current objective

No active goal. Complete the kickoff discussion.

## Confirmed state

### Completed

- The portable project structure was initialized.

### In progress

- Project contract and stable context are not yet baselined.

### Blocked

- None known.

## Changed artifacts

| Path | Change | State |
|---|---|---|
| Project memory files | Initialized from Factory template | UNVERIFIED |

## Verification evidence

| Check | Result | Executed at / artifact version | Basis / exit code | Evidence or command |
|---|---|---|---|---|
| Initial project validation | NOT_RUN | none | none | `python .ai/project_runtime.py doctor` |

## Decisions referenced

- None yet.

## Risks and unknowns

- Contract, stable context, and active goal still contain unknown fields.

## Next actions

1. Conduct the kickoff discussion.
2. Materialize the agreed Contract, Context, Decisions, and Active Goal.
3. Validate and commit the discussion before implementation.

## User decisions required

- Approve the project contract before Goal mode begins.
