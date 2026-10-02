# Memory Schema

Authoritative governance for `.agents/memory/` files. `lint_memory.sh` enforces these rules.

## Required frontmatter

Every memory file (except MEMORY.md, SCHEMA.md, README.md) must have:

```yaml
---
name: <short title>
description: <one-line summary used for relevance ranking>
type: user | feedback | project | reference
---
```

## Types

| Type | Purpose | Body structure |
|---|---|---|
| `user` | About the human — role, preferences, expertise | Free-form |
| `feedback` | Rules/corrections the user has given | **Why:** + **How to apply:** |
| `project` | Current-state facts — goals, deadlines, decisions | **Why:** + **How to apply:** |
| `reference` | Pointers to external systems — URLs, dashboards | Free-form |

## File naming

Convention (not enforced by lint): `<type>_<slug>.md` — lowercase, hyphenated slug.

Examples: `user_role.md`, `feedback_testing.md`, `project_deadline.md`, `reference_api-docs.md`

## Index

Every memory file must appear in `MEMORY.md` as a one-line bullet:

```markdown
- [Title](filename.md) — short description
```

Keep lines under ~150 characters and files under 100 lines (`LINT_MEMORY_MAX_LINES` overrides).
One fact per file — move longer material to the vault.
`lint_memory.sh` enforces the size cap and checks for orphaned files and stale index entries.

## What NOT to store

- Code patterns or architecture (derivable from the repo)
- Git history (`git log`)
- Ephemeral task state (use TODOs)
- Anything already in `CLAUDE.md` or `project-init.md`
- Large documents (put those in `vault/`)

## Map, not territory

A memory — like any cached artifact (a diagram, a doc) — may assert only facts
that are **mechanically verifiable** (repo-relative paths, module boundaries,
ownership) or **immune to change** (a rationale, a decision and its date).
Volatile specifics — tool names, thresholds, version pins, line numbers — must
be **pointed at, not restated**: name the file that owns the value, don't copy
the value in. Restated "territory" rots silently the moment its source moves,
and no gate catches it: `lint_memory.sh` can flag a dangling backtick-`path.ext`
but not a threshold that has drifted from the `justfile`. Cheap-to-regenerate
artifacts get regenerated, not cached; underivable rationale goes here or to an ADR.

## Relationship to vault

`memory/` holds small structured facts for fast agent recall. `vault/` holds richer human-authored documentation (Obsidian). When a vault note distills into a reusable fact, create a memory file and link back to the vault note for context.
