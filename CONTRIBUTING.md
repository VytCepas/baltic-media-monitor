# Contributing to baltic-media-monitor

GDELT-based monitor of how Russian, foreign and Baltic outlets cover the Baltics (CCBD coursework)

This project was scaffolded with [project-init](https://github.com/VytCepas/project-init): agent
instructions live in [AGENTS.md](AGENTS.md) (canonical; `CLAUDE.md`
redirects there), and the conventions below bind humans and
coding agents alike. Claude Code gets deterministic enforcement (hooks);
other agents and humans get the same rules via git hooks and CI.

## Setup

```bash
just setup
```

Install the repo git hooks once per clone — they enforce commit-message
format and secret scanning locally:

```bash
.agents/scripts/install_hooks.sh
```

## Commands

The justfile is the canonical command surface — `just --list` shows every
recipe. CI runs the same recipes, so if `just lint` and `just test` pass
locally, CI agrees.

## Commits and PRs

Use Conventional Commit messages (`feat:`/`fix:`/`chore:`/`docs:`/`test:`).
Open a pull request, keep CI green, and respond to every review comment —
including bot reviews — either by fixing or by explaining why not.

## Security

See [SECURITY.md](SECURITY.md) for how to report vulnerabilities —
please do not open public issues for security problems.
