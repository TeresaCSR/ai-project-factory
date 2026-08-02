# AI Takeover Prompt

Give this prompt to a new Agent. If it can access the project directory, do not
paste the old chat.

```text
You are taking over an AI Project Factory project.

First state your capability tier: full local Agent, file-only Agent, or
chat/API-only model. Read AI_START_HERE.md and follow its mode-specific reading
order. Verify HANDOFF.md against actual artifacts and reproducible evidence.

Before working, report only:
1. current mode and goal status;
2. approved Contract and current Active Goal;
3. verified state versus unknown or stale claims;
4. the next concrete action.

In Goal mode, ordinary user messages are steering and do not pause execution.
Stop only for the conditions defined in AI_START_HERE.md. Before a material
pause, compact, provider switch, blocker, or completion, update the semantic
Handoff and run the project checkpoint command.

Do not invent missing context. Do not claim that a command, test, render, or
simulation ran unless you can supply the evidence.
```

For a chat/API-only model, export `AI_CONTEXT_BUNDLE_*.md` and upload only the
task artifacts it truly needs. The bundle does not grant local filesystem or
tool access.
