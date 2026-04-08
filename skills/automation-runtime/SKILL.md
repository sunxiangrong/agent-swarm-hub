---
name: automation-runtime
description: Use when the user wants bounded in-agent automation for the current project, including one-step continuation with `/autostep`, bounded monitor loops with `/automonitor`, or deciding when runtime health should stop, repair, or continue an automated run.
---

# Automation Runtime

Use this skill when the task is to keep working inside the current project with bounded automation rather than a one-off manual reply.

## Use this skill for

- deciding whether to run `/autostep` or `/automonitor`
- deciding whether the user wants watch-only monitoring versus active continuation
- explaining why automation should stop or continue
- running bounded project automation under current runtime health
- checking whether `--until-complete` should be used

## Decision rule

- Use `/autostep [provider] [--explain]` when the next step is clear and only one meaningful increment is needed.
- Use `/automonitor [--apply] [--auto-continue] [--until-complete] [--cycles N] [--interval N]` when the project should be watched for several bounded cycles, with heartbeat, optional repair, and optional single-step continuation per cycle.
- If the user asks to "check progress every so often", "watch task status", or similar watch-only intent, treat it as monitoring first, not continuation first.
- Prefer `--explain` first if the next step is unclear.

## Start gate

- Before starting bounded automation, derive one concrete next step for the current project.
- If the projected next step is still generic, monitor-like, or ambiguous, do not start direct execution yet.
- In that case, explain the proposed next step first and ask for confirmation before switching to execution.
- Preferred confirmation flow:
  1. show the concrete next step
  2. ask whether to proceed
  3. only then run `/autostep` or `/automonitor ... --auto-continue`

## Watch interaction rule

- When the user wants periodic status checks but does not specify timing, ask one short clarifying question for the interval.
- Preferred question style:
  - `隔多久看一次？`
- If the user gives only one number, interpret it as seconds unless they explicitly say minutes.
- For watch-only requests, prefer monitor behavior without `--auto-continue`.
- For "watch and keep pushing forward" requests, use monitor behavior with `--apply --auto-continue`, and add `--until-complete` only when the user clearly wants the task to be driven toward a stopping condition.
- Keep monitor loops bounded. Do not start an unbounded watch loop.
- Default monitoring should stay project-scoped. Only use all-project/runtime-wide heartbeat sweeps when the user is explicitly doing maintenance or cleanup across projects.

## Safety gates

- Stop automation if runtime health is blocked:
  - `quarantined`
  - `unhealthy`
  - `orphan-running`
  - `missing-binding-process`
- Keep automation bounded:
  - one `/autostep` executes at most one increment
  - one `/automonitor` cycle triggers at most one auto-continue per eligible project
- `--until-complete` may stop early when:
  - no further stable auto-continue candidate is available
  - structured completion check returns `completed`
  - structured completion check returns `blocked`
  - structured completion check returns `needs_confirmation`

## Runtime commands

- Chat entry:
  - `/autostep [provider] [--explain]`
  - `/automonitor [--apply] [--auto-continue] [--until-complete] [--cycles N] [--interval N]`
- CLI entry:
  - `project-sessions auto-continue <project> [--provider ...] [--explain]`
  - `project-sessions monitor <project> [--apply] [--auto-continue] [--until-complete] [--cycles N] [--interval N]`

## Operating pattern

1. Read current project state and runtime health.
2. Choose `autostep` for one bounded increment, or `automonitor` for bounded watch-and-continue loops.
3. Let runtime health gate unsafe paths before automation runs.
4. After execution, read updated project summary or dashboard state before deciding on another automation action.
