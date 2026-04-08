# ash-workbench

Use this skill when the task is about operating the `ash-workbench` runtime
project: tmux workbench layout, ccb-linked provider panes, ssh panes, bridge
policy, pane visibility, or follow-up/monitor orchestration.

## Purpose

`ash-workbench` is not a business project. It is the runtime control-plane
project for:

- tmux workbench sessions
- `agent:*` panes
- `ssh:*` panes
- optional `manual` pane
- optional secondary agent pane
- ccb/tmux coordination
- bridge policy and pane visibility

## Default operating model

- Start with the lightest layout that fits the task.
- Default layout:
  - one primary agent pane
  - one ssh pane
- Add a `manual` pane only when human takeover is likely.
- Add a second agent pane only when planning/supervision is worth the extra
  surface area.

## Preferred commands

- Open a light workbench:
  - `project-sessions bridge-workbench <project> --provider codex --ssh-target xinong --no-manual`
- Open a standard workbench:
  - `project-sessions bridge-workbench <project> --provider codex --ssh-target xinong`
- Add a supervisory planner:
  - `project-sessions bridge-workbench <project> --provider codex --ssh-target xinong --secondary-agent claude`
- Inspect workbench state:
  - `project-sessions bridge-status <project> --provider codex --exports`
- Adjust default ssh targets:
  - `project-sessions bridge-policy <project> --set-ssh-target xinong`

## Rules

- Treat pane count as a cost. Prefer fewer panes unless the workflow clearly
  benefits from more separation.
- Keep runtime/tmux/bridge state in `ash-workbench`; keep business logic and
  domain conclusions in the business project.
- Read pane state before acting. Prefer `read -> act -> read`.
- Respect bridge policy boundaries:
  - pane boundaries
  - path boundaries
  - command boundaries
- Treat `manual` as read-only unless the operator explicitly changes policy.

## Memory focus

The important long-lived memory for `ash-workbench` is:

- default layout patterns
- preferred ssh targets
- pane role conventions
- bridge policy defaults
- ccb/tmux coordination rules
- follow-up/monitor operating habits

Do not turn this project into a duplicate of business-project summaries.
