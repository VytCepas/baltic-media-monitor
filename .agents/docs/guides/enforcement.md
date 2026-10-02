# Enforcement guide — guards, escape hatches, and per-surface wiring

Reference detail for the enforcement rules stated in [AGENTS.md](../../../AGENTS.md).
The rules live there; this guide holds the mechanics so the always-loaded
instruction file stays lean (PI-657, epic #641).

## `prod_guard` — destructive infra/DB commands

Destructive infra/DB commands are flagged by the `prod_guard` hook: it **asks
interactively** in normal sessions and **hard-blocks in fully autonomous
mode**. Escape hatch: `safety.allow` in `.agents/config.yaml`.

On the non-Claude surfaces (Codex/Cursor/Antigravity) the same guard fires via
the shared adapter (`.agents/hooks/agent_guard_adapter.py`) and hard-blocks
wherever the surface actually enforces the hook (those surfaces are
non-interactive, so "ask" isn't possible). Hook enforcement is best-effort and
surface-dependent — e.g. some Codex versions gate project-scoped hooks behind
a one-time trust/enable step — so treat it as a guardrail, not a guarantee
(PI-394). The real guarantee is credential separation: production credentials
belong to review-gated CI jobs, never to a shell an agent runs in (ADR-012).

## `package_guard` — supply-chain install check

`uv add` / `bun add` / `pip install` / `npm install` / `cargo add` are checked
against the PyPI/npm/crates.io registry before the install runs: a name that
doesn't exist (likely a typo or hallucinated dependency) or that's
suspiciously close to a popular package (possible typosquat) is flagged by the
`package_guard` hook — same ask/hard-block split as `prod_guard`. Network
failures fail open (never blocks an install just because the registry couldn't
be reached). A guardrail, not a substitute for lockfile pinning and hash
verification (PI-564).

