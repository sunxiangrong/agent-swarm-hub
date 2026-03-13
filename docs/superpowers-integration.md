# Superpowers Integration

## Goal

Use `Superpowers` selectively to improve task understanding and decomposition without letting it take over runtime coordination.

## Recommended Integration Boundary

Treat `Superpowers` as a policy library for Claude Code:

- before coordination: improve understanding and plan quality
- during execution: help with debugging or review
- before completion: improve verification

Do not let it directly own:

- remote chat routing
- tmux session lifecycle
- child-agent escalation rules
- spokesperson message policy

## First-Pass Skills

Enable these first:

- `brainstorming`
- `writing-plans`
- `systematic-debugging`
- `requesting-code-review`
- `verification-before-completion`

## Deferred Skills

Leave these off until the coordinator contract stabilizes:

- `subagent-driven-development`
- `test-driven-development`
- `using-git-worktrees`

Those flows can conflict with a custom swarm runtime if introduced too early.

## Installation Policy

Prefer the upstream installation instructions from the `obra/superpowers` repository.

This repository should store only:

- integration notes
- selected skill list
- wrapper scripts if needed
- local policy docs

It should not vendor the upstream project by default.
