# Validation report — v0.5.3

Date: 2026-08-02
Environment: Windows, Python 3.13, Codex CLI 0.146.0.
Declared support: Python 3.10+.

## Summary

v0.5.3 fixes two defects that only a real trial exposed.

**1. Injected history did not render.** v0.5.2 used `thread/inject_items` to
write the original input and the start card. The items existed in the model's
history, but Codex Desktop reported `turns=[]`, so opening the task showed an
empty pane. Fast, and useless.

The fix is a strictly bounded but genuinely real bootstrap turn: a standard
`userMessage` carries the full project input, and the model replies with the
start card and nothing else -- no file reads, no tool calls. The task opens as
soon as `turn/start` succeeds, so the card is generated where the user can
watch it, in about ten seconds. The real interview begins when they reply
`continue`.

**2. Console windows still flashed.** `CREATE_NO_WINDOW` did not cover every
console-creation path used by Codex and its nested Git helpers. All Windows
helpers now use `CREATE_NEW_CONSOLE` with `STARTF_USESHOWWINDOW`/`SW_HIDE`, so
the console is hidden from the first frame. Sampling the whole process tree and
all top-level windows at 20 ms intervals -- across project creation, Git, the
Codex App Server, and model startup -- recorded zero visible windows.

## Evidence for the diagnosis

| Observation | Evidence | Conclusion |
|---|---|---|
| Task opened with an empty right pane | Saved session contained injected user/assistant items, but the app read `turns=[]` | `thread/inject_items` is not a substitute for a turn the desktop app can render |
| A task with the right name existed but could not be talked to | Task idle, no standard first turn | Factory was reporting "history injected" as "task ready" |
| Console window still appeared and flickered | Process tracing under `CREATE_NO_WINDOW` still showed conhost for Codex and Git | The old hiding strategy did not cover the full child process tree |

## Fixes and how each was verified

| Defect | Fix | Evidence |
|---|---|---|
| Injected history invisible, task blank | Use a standard `turn/start`; one turn made of a real user and agent item | A persistent internal task read back `turn_count=1`, `item_count=2`, of types `userMessage` and `agentMessage`, then was deleted |
| Long hidden reasoning reintroduced | The bootstrap turn emits a fixed card only -- no files, no tools -- and the task opens as soon as the turn exists | Live end-to-end: 10.058 s, visible to the user throughout |
| Console window and flicker | Factory helpers use a hidden new console from creation; the App Server no longer shells out to `codex mcp list` | 20 ms process-tree sampling: `visible_windows=[]` |
| Temporary host loading user tools | Plugins, apps, shell, code mode, in-app browser, and both built-in desktop MCP servers disabled | Live first turn requested no external tool approvals; the card matched exactly |
| Failed startup left junk tasks | On timeout or a non-completed status, interrupt first, then delete the incomplete task and fall back to a prefilled draft | Fault-injection regression |
| Waiting for the task to appear in Recents | Open the deep link as soon as `turn/start` returns, without waiting for the model | Ordering regression: create → title → turn/start → open → completed |

## Test results

| Area | Result | Evidence |
|---|---|---|
| Full unit and fault-injection suite | PASS | `python -X utf8 -B -m unittest discover -s tests -v`, 82/82, 133.9 s |
| Launch chain and GUI specifics | PASS | 23/23: real turn, open ordering, hidden console, failure cleanup, GUI unlock |
| Live App Server, ephemeral | PASS | `completed` in 10.058 s; temporary folder removed |
| Standard first turn read back | PASS | 1 turn, 2 items; agent card matched expectations; internal task deleted afterwards |
| Process subtree and window tracking | PASS | 20 ms sampling; Codex, Git, and conhost all covered; zero visible windows |
| GUI smoke test | PASS | Exit 0 from both the source tree and the installed `current` |
| Skill structure | PASS | `quick_validate.py` reports valid |
| Python 3.10 grammar | PASS | 22 Python/PYW candidate files, 0 failures |
| Wheel and ZIP regressions | PASS | Deterministic build, manifest, cold start from a clean venv and from the extracted archive |
| Desktop deployment | PASS | Deployed twice in a row; 48 files; same stable shortcut and H2 icon |

## Release artifacts

- Version `0.5.3`
- Wheel `ai_project_factory_demo-0.5.3-py3-none-any.whl`, 68,674 bytes,
  SHA-256 `56081d34e8bdced3a5e38908bcc1c6af991d9c0927ae8b67435454445453652c`
- Portable `AI-Project-Factory-Portable-v0.5.3.zip`, 177,054 bytes,
  SHA-256 `102f0cd3e0d25c36039c0df5eff3f082d07d92b83249ba4c9871dbd4e3f22239`
- Desktop channel `%LOCALAPPDATA%\AI Project Factory\current`
- One unversioned `Desktop\AI Project Factory.lnk`

## Limits of this report

1. v0.5.3 guarantees a standard, visible first turn for tasks created or
   restarted from now on. It does not rewrite tasks that already exist, and
   items injected by v0.5.2 are not retrofitted into visible history. Later
   turns in those tasks still work normally.
2. The start card still requires one short model generation, measured at about
   ten seconds. That is a deliberate trade against v0.5.1's roughly 153-second
   full project analysis: this turn only confirms the handoff, and the task is
   already open while it happens.
3. The full matrix ran locally on Python 3.13. Python 3.10 passed a grammar
   check but not the full local matrix; CI covers 3.10 through 3.13 on Ubuntu,
   Windows, and macOS.
