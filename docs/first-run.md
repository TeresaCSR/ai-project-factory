# First run

Ten minutes, no terminal. Nothing here modifies your global Codex or Claude
configuration.

If you want to check the portable form at the same time, unzip the release
archive first and launch from the extracted folder. The launcher in a source
checkout works identically.

## A. Create

1. Double-click `AI Project Factory.cmd`.
2. Open **Project console**. Any Factory projects already in the default
   folder should list their mode, goal, handoff revision, and last update.
3. When you start a real project, fill in the name and, optionally, an initial
   idea, then click **Create and open a Codex discussion**. The project and a
   standard Codex first turn are created and opened immediately; the start
   card usually finishes in 10-20 seconds. You should see no console window at
   any point, and no task with an empty right-hand pane.
4. Back in the console, click **Project state**. Expect:
   - mode: Discussion
   - no goal yet
   - handoff verified as current

## B. Start the interview

1. The Codex task should show an ordinary first turn: a user message carrying
   your original input, followed by a clearly labelled Factory start card. The
   task title includes the project name.
2. Reply `continue`, or just add requirements. The real interview happens in
   that same visible task. Only if the interface explicitly says it fell back
   to a prefilled draft do you need to check and send the prompt yourself --
   the same text is on your clipboard.
3. Expect the agent to read `AI_START_HERE.md` first, then ask about goals,
   deliverables, acceptance criteria, constraints, and unknowns -- not to pick
   a stack on your behalf.
4. The project stays in Discussion until you say to start building.

## C. Compact, or switch chats

1. Click **Copy: prepare to switch** and paste it to the current agent.
2. The agent updates `HANDOFF.md`, runs checkpoint and doctor itself, and
   reports whether switching is safe.
3. Back in the GUI, click **Refresh handoff checkpoint**, then
   **Full validation**.
4. Click **Copy: new agent takeover** and paste it into a new chat.
5. The new agent should open by reporting the mode, the approved goal, what is
   verified, and the next action -- without relying on the old conversation.

## D. Switch to Claude Code, or to a web model

- **Local Claude Code:** open the same project folder. No export needed.
- **Web or bare API:** click **Export web / API bundle**, save it outside the
  project folder, and upload only what the current task genuinely needs.
- **Install local agent integration** is optional. It installs user-level
  skills on this machine, uploads nothing, and does not touch cloud sessions.
  Reopen your agent session afterwards. If you later move the Factory folder,
  install again from the new location.

## What "it worked" looks like

- You never had to type a Python command.
- Discussion and Goal stayed distinct.
- After compacting or switching agents, the new session recovered from the
  project files rather than from model memory.
- The local-agent path and the web/API bundle path stayed clearly separate.

Delete the trial project whenever you like. Factory does not upload or back it
up anywhere.
