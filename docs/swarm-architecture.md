# Runtime-Coordinated Swarm Architecture

## What Swarm Means Here

In this repository, `swarm` means:

- agents have different specialties, not rigid rank
- each agent can refine its assigned work inside its own domain
- coordination happens through shared task state and events
- external users do not see the full internal chatter

This is different from a strict manager-employee model.

In a manager model:

- one top agent decomposes everything
- child agents mostly execute
- planning quality bottlenecks at the top

In this swarm model:

- the runtime coordinator creates the initial task graph
- specialists can split their own task further
- disagreements and blockers become explicit events
- the spokesperson emits one consistent remote-facing view

## Two-Layer Operating Model

```text
Remote Chat Tools
  Lark / Telegram
        |
        v
Spokesperson Layer
  Claude Code default summary / task assignment voice
        |
        v
Runtime Coordinator Layer
  task graph / escalation / state / routing
        |
        v
Swarm Layer
  researcher / planner / builder / critic / synthesizer
        |
        v
Execution Layer
  ccb / tmux / local tools / model providers
```

The design rule is:

- internal system acts like a swarm
- external system behaves like a single accountable operator

## Why This Fits CCB

`ccb` already provides visible, controllable multi-agent execution. What it lacks is a stable orchestration layer that decides:

- what the current task graph is
- which events matter externally
- when a child opinion becomes an escalation
- how to convert raw internal state into one clean remote update

That is the purpose of this repository.

## Stable Technical Route

Recommended stack order:

1. `ccb + tmux` as the execution substrate
2. `cc-connect` as the remote transport layer for Telegram and Lark
3. this repository as the runtime coordinator and spokesperson policy layer
4. `Superpowers` as an optional planning/review enhancement layer

This keeps the mature local execution path while minimizing duplicate orchestration logic.

## Core Objects

### Task

A unit of work with:

- id
- title
- role
- status
- parent task id
- notes

### Event

A structured signal emitted by an agent or the runtime coordinator:

- `task_started`
- `task_split`
- `task_completed`
- `task_blocked`
- `risk_detected`
- `dissent_raised`
- `need_input`
- `final_candidate_ready`

### Escalation

A decision that a specific event should be shown in remote chat.

Default escalation classes:

- `BLOCKER`
- `RISK`
- `DISSENT`
- `REQUEST_INPUT`
- `IMPORTANT_FINDING`

### Spokesperson Summary

The only normal output that remote chat should receive:

- task accepted
- current stage
- notable progress
- blockers or risks
- next action
- final result

## Role Mapping

Suggested swarm roles:

- `runtime coordinator`: maintains task graph and global priorities
- `researcher`: gathers evidence and references
- `planner`: expands structure and approach options
- `builder`: produces draft/code/output
- `critic`: looks for flaws, regressions, contradictions
- `synthesizer`: merges results into one coherent conclusion
- `spokesperson`: writes the external update

One runtime may host several roles. Claude Code can reasonably cover:

- synthesizer
- spokesperson
- optional high-capability coordinator override

## Superpowers Positioning

`Superpowers` should improve reasoning quality, not replace the runtime coordinator.

Recommended use:

- `brainstorming`: before the first task graph
- `writing-plans`: to shape a task graph
- `systematic-debugging`: when the swarm stalls
- `requesting-code-review`: before final technical output
- `verification-before-completion`: before remote completion notice

## Repository Boundary

This repo owns policy and orchestration logic.

`agent-env` should later install:

- this repo
- optional Superpowers setup
- `cc-connect`
- local config overlays

That separation keeps the system portable without burying business logic inside environment backup code.
