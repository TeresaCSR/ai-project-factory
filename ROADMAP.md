# Roadmap

Candidate directions, not an approved contract. Priority follows what real
project use actually turns up.

## Next

### P0: run one real project for two or three days

A full acceptance pass has already been done on a fresh software project: GUI
creation, discussion commit, goal, uninterrupted steering, stale-handoff
detection, compact checkpoint, cold takeover in isolation, completion, and the
API bundle all passed.

The next step is not more state fields. It is living with one real project for
two or three days and watching two things: whether the interview feels natural,
and what maintaining the handoff costs over time.

### Done: real one-click Codex tasks

v0.5.0 moved to the official Codex App Server and created real tasks. v0.5.1
waited for a hidden bootstrap to finish, which showed up in real use as roughly
153 seconds of grey waiting. v0.5.2's `thread/inject_items` was fast, but Codex
Desktop does not render injected items as a standard turn, so the third trial
produced a task with a blank pane.

v0.5.3 uses a strictly bounded but real bootstrap turn: the standard user
message keeps the full input, the model replies with the start card only, and
the task opens as soon as `turn/start` returns -- usually 10-20 seconds.
Windows child processes use a hidden console from creation; 20 ms live sampling
found no visible console window. A failure deletes the incomplete task and
falls back to a prefilled draft.

### Done: project console and start seed

The default project root now lists recent Factory projects with their
discussion/goal state, handoff revision, and last update. The create page takes
an optional initial idea, labelled in the prompt as interview input rather than
an approved contract. Fixed side navigation replaced adjacent tabs, whose size
and selected state were easy to misread.

## Later

### P1: safely upgrading existing projects

Each project carries a self-contained runtime today. That is what makes it
portable, and the cost is that old projects do not pick up later bug fixes.
An explicit "check / upgrade project runtime" step could:

- upgrade only the Factory-managed `.ai` tools, never the contract, context, or
  decisions;
- back up, verify current hashes, and check project state before starting;
- support dry-run, a version compatibility matrix, and rollback on failure;
- keep the constitution pinned to its creation-time version unless the user
  explicitly approves migrating it.

### P1: a genuinely standalone desktop build

A fixed install directory, a single unversioned shortcut, a smoke test before
updating, rollback on failure, and a stable icon path all landed in v0.3.3 and
carried the real Codex launch in v0.5.3 without changes. That solves "later
updates still map to the same desktop entry", but it still needs Python 3.10+.
If real use proves stable, the remaining work is:

- a single-file or single-folder Windows executable;
- code signing, version metadata, and a visible upgrade prompt;
- a launch log and one-click diagnostics;
- macOS and Linux launchers.

### P1: a Git baseline and backup-readiness wizard

"Works when you switch locally" and "can migrate by clone" are different
properties, and conflating them loses work. A wizard could show:

- full-folder copy: available or not;
- first Git commit: present or not;
- remote backup: configured or not;
- memory and runtime files: ignored, untracked, or tracked.

Any commit, remote creation, or push stays explicitly authorised.

### P2: a local host for other model APIs

Do not write any one vendor's API into the project protocol. Build a
replaceable host instead:

- reads the same project folder and `AI_START_HERE.md`;
- uses a model API as the reasoning backend;
- provides files, commands, browsing, and permissions itself;
- degrades to a context bundle when it cannot, rather than pretending to have
  local tools.

### P2: host-aware checkpoints

If a runtime ever offers a reliable pre-compact or session hook, a thin adapter
could trigger "update the semantic handoff → checkpoint → doctor"
automatically. The core protocol still cannot assume every host has one.

### P3: multiple people, multiple agents

Worth considering only once a real project needs parallel writes: a task
database, handoff merging, branch ownership, conflict resolution. Today's "one
handoff, one coordinating writer" is simpler and much less able to manufacture
fake progress.

## Explicitly not planned

- A separate template or state machine per model vendor.
- Updating the handoff every turn.
- Putting whole chat transcripts, personal memory, or account sessions into a
  project.
- Growing into a large workflow platform before real use justifies it.
- Claiming a bare API can reach local files because it can write code.
