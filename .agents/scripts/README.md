# `.agents/scripts/`

Helper scripts invoked by the user, by hooks, or by agents via the Bash tool. Prefer bash or python stdlib. Document each script with a one-line header comment.

## Available scripts

- **`install_hooks.sh`** — Symlink or copy git hooks from `.github/hooks/` to `.git/hooks/`
- **`lint_context_budget.sh`** — Fail when always-loaded context files outgrow their token budgets (CLAUDE.md/AGENTS.md 200 lines, SKILL.md 500); part of `just lint`

