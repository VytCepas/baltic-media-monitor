# Project: baltic-media-monitor

> GDELT-based monitor of how Russian, foreign and Baltic outlets cover the Baltics (CCBD coursework)

Scaffolded with [project-init](https://github.com/VytCepas/project-init) on 2026-10-01.

| | |
|---|---|
| Language | python |
| Memory stack | auto |
| MCPs | none |

## Workflow

1. **Start of session** — read [`memory/MEMORY.md`](memory/MEMORY.md), then check [`.agents/docs/adr/`](docs/adr/) for relevant decisions.
2. **During work** — permanent decisions → [`.agents/docs/adr/`](docs/adr/); reusable facts → [`memory/`](memory/).

## Tools

| Type | Name | Purpose |
|---|---|---|
| Command | `/status` | git state, recent commits, open TODOs |
| Command | `/review` | code review of staged changes or a file |
| Command | `/plan <task>` | acceptance tests first, then implementation plan |
| Skill | `report_upstream_issue` | report a bug — routes tooling bugs upstream to project-init, keeps project bugs local |
| Skill | `token_efficiency` | token-frugal working habits for long coding/debugging sessions |
| Skill | `checkpoint` | checkpoint-and-clear session handoff (gitignored) |
| Skill | `diagram` | draw/iterate diagrams (Mermaid-first, committed source) |
| Skill | `verify-test-strength` | mutation-test a load-bearing test until survivors are killed |
| Skill | `local_ci` | Actions minutes lockout → self-hosted runner escape hatch |
| Skill | `add_hook` | add a new deterministic hook |
| Skill | `add_command` | add a new slash command |
| Agent | `reviewer` | code review specialist |
| Agent | `researcher` | codebase explorer |

Hooks run automatically: `pre_commit_gate`, `post_edit_lint`, `prod_guard` — supplied by the `project-init-workflow` plugin (enabled in `.agents/settings.json` under `enabledPlugins`, not a local `hooks` block); the scripts live under the plugin's `CLAUDE_PLUGIN_ROOT`/hooks, so edit the plugin to change them. Secret scanning and lifecycle gating are enforced agent-agnostically by git hooks (gitleaks pre-commit, commit-msg, pre-push — installed via `.agents/scripts/install_hooks.sh`) and mirrored in CI; the `security-guidance` plugin provides Claude-side guidance.

**Rules** (`.agents/rules/`): per-filetype conventions loaded automatically by Claude Code when you open a matching file.

## Coding standards

- No comments unless the WHY is non-obvious.
- No premature abstractions — three similar lines before extracting.
- No error handling for impossible scenarios.
- No backwards-compatibility shims — delete removed code.
- Prefer editing existing files over creating new ones.
- `just lint` must pass before closing a task (`just --list` shows all recipes).


## Test-first for design

When a task shapes an interface or a fix, drive it with `/plan` — write the
acceptance tests first (the test is the contract), then implement:

1. `/plan <task>` → acceptance tests (red)
2. Commit failing tests
3. Implement until green
4. Lint and clean up

Then prove each guard can fail: break what it checks, watch it fail, then
restore — a test that passes on broken code is worse than none.

Test conventions: one assertion per test, name as `test_<unit>_<scenario>`, run with `uv run pytest`. Real DB/API instances — no mocks.


## Conventions

- Hooks and scripts prefer bash/python. LLM calls only for generative steps.
- `AGENTS.md` is the canonical root instruction file (the standard most agents read natively). `CLAUDE.md` redirects to it.
- Everything else lives under `.agents/`.

## Compact Instructions

Compaction is a Claude Code mechanism; the canonical preserve-list lives in
[CLAUDE.md](../CLAUDE.md) (single source — do not duplicate it here).
