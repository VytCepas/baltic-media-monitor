# justfile — the canonical command interface (scaffolded by project-init).
# `just --list` shows every recipe. Recipes are thin wrappers — logic lives
# in the tools and their configs, never in this file.

# install/sync dev dependencies (PEP 735 dependency-group; add tools with `uv add --dev`).
# A fresh scaffold has no pyproject.toml (or one without [dependency-groups])
# yet — `uv sync --group dev` hard-fails on both, which would break CI's first
# step before the day-one guards in typecheck/test-cov can even run.
[doc("install/sync dev dependencies (PEP 735 dependency-group; add tools with `uv add --dev`).")]
setup:
    sh -c 'if [ ! -f pyproject.toml ]; then echo "No pyproject.toml yet — nothing to sync."; elif grep -q "^\[dependency-groups\]" pyproject.toml; then uv sync --group dev; else uv sync; fi'

# lint project code (docstring + complexity gates per ruff.toml).
# `ruff format --check` verifies formatting without writing — `just format`
# writes. Without this, an unformatted file merged green (#726).
[doc("lint project code (docstring + complexity gates per ruff.toml).")]
lint:
    uv run ruff check .
    uv run ruff format --check .
    sh -c 'if command -v shellcheck >/dev/null 2>&1; then find .agents infra -name "*.sh" -exec shellcheck -S error -x {} +; else echo "shellcheck not installed — skipping shell lint (CI still runs it). Install: https://github.com/koalaman/shellcheck#installing or run \`mise install\`."; fi'
    sh -c 'if command -v shfmt >/dev/null 2>&1; then find .agents -name "*.sh" -exec shfmt -d -i 2 {} +; else echo "shfmt not installed — skipping shfmt check (CI still runs it). Install: https://github.com/mvdan/sh#shfmt or run \`mise install\`."; fi'
    bash .agents/scripts/lint_context_budget.sh
    uv run python .agents/scripts/check_test_contract.py exit-codes
    uv run --with pytest python .agents/scripts/check_test_contract.py discovery

# static type check (strict mode per mypy.ini; add mypy with `uv add --dev mypy`)
# no-op on a fresh scaffold with no src/ yet — mypy errors on a missing path.
# --install-types fetches missing dependency stubs (types-PyYAML etc.) so
# untyped deps don't fail the strict gate with import-untyped (#592); it
# shells out to pip, which uv-managed environments omit — hence --with pip.
[doc("static type check (strict mode per mypy.ini; add mypy with `uv add --dev mypy`)")]
typecheck:
    if [ -d src ]; then uv run --with "mypy>=1.10" --with pip mypy --install-types --non-interactive src/; else echo "No src/ directory yet — nothing to type-check."; fi

# auto-format project code
format:
    uv run ruff format .

# run the test suite (xdist pulled in on demand so -n works without declaring it).
# The cross-repo test contract (PI-1044): it ends with `<project>: N passed, M failed`,
# printed by the root conftest.py, and exits with pytest's own code.
[doc("run the test suite; ends with `<project>: N passed, M failed` (the test contract)")]
test:
    sh -c 'if find tests -type f \( -name "test_*.py" -o -name "*_test.py" \) 2>/dev/null | grep -q .; then uv run --with pytest-xdist pytest -n auto --tb=short -q; else echo "No test files yet — nothing to test."; echo "baltic-media-monitor: 0 passed, 0 failed"; fi'

# fail-fast quiet run for the edit-test loop (token-efficiency; PI-641) —
# stops at the first failure so agents ingest one traceback, not the suite's
[doc("fail-fast quiet run for the edit-test loop (token-efficiency; PI-641)")]
test-quick:
    sh -c 'if find tests -type f \( -name "test_*.py" -o -name "*_test.py" \) 2>/dev/null | grep -q .; then uv run --with pytest pytest -x -q --tb=short; else echo "No test files yet — nothing to test."; fi'

# tests with the coverage gate (CI always runs this, not `test` — PI-569).
# without src/ yet, still run the plain test suite (tests/ may exist before
# src/ does) — only the coverage instrumentation/threshold is skipped, since
# 0% coverage on zero application code would trip --cov-fail-under before any
# code exists. Silently skipping pytest entirely here would let a real test
# failure through `just ci` unnoticed.
[doc("tests with the coverage gate (CI always runs this, not `test` — PI-569).")]
test-cov:
    sh -c 'if [ -d src ]; then uv run --with pytest-xdist --with pytest-cov pytest -n auto --tb=short -q --cov=src --cov-fail-under=80; elif find tests -type f \( -name "test_*.py" -o -name "*_test.py" \) 2>/dev/null | grep -q .; then uv run --with pytest-xdist pytest -n auto --tb=short -q; else echo "No src/ or test files yet — nothing to test."; fi'

# mutation testing on core logic (slow; requires [tool.mutmut] source_paths
# in your own pyproject.toml, scoped to deterministic pure-logic modules —
# skip I/O-heavy code, hooks, template renderers). CI runs this nightly.
[doc("mutation testing on core logic")]
test-mutation:
    uv run --with mutmut mutmut run
    uv run --with mutmut mutmut export-cicd-stats

# dependency vulnerability scan against known CVEs/advisories (PI-568).
# complements package_guard.py, which only blocks installing a package that
# doesn't exist or looks typosquatted — this catches a real, correctly-
# spelled dependency with a known vulnerability already in the lockfile.
[doc("dependency vulnerability scan against known CVEs/advisories (PI-568).")]
audit:
    sh -c 'if [ -f pyproject.toml ] || [ -f requirements.txt ] || [ -f uv.lock ]; then uv run --with pip-audit pip-audit; else echo "No Python dependency manifest yet — nothing to audit."; fi'

# generate a CycloneDX SBOM of the runtime dependency tree (#574). release.yml
# attaches this to GitHub Releases; run locally on demand. --no-dev so it
# reflects what ships; uvx keeps cyclonedx-py out of the scanned .venv.
[doc("generate a CycloneDX SBOM of the runtime dependency tree (#574)")]
sbom:
    uv sync --no-dev
    uvx --from cyclonedx-bom cyclonedx-py environment .venv -o sbom.cdx.json

# dependency license compliance scan (#579) — fail on copyleft (GPL/AGPL; also
# LGPL, since --partial-match is substring-based). Tune the --fail-on deny-list
# to your policy. Non-blocking in CI initially (see ci.yml's license-scan job).
[doc("dependency license compliance scan (#579)")]
license:
    uv run --with pip-licenses pip-licenses --from=mixed --fail-on "GPL;AGPL" --partial-match

# property-based tests with Hypothesis (#580). Property tests are opt-in per file
# (import hypothesis); this recipe makes Hypothesis available without adding it as
# a permanent dependency. Pattern/tooling, NOT a blocking gate: CI runs this in
# the nightly `fuzz` job, never on a PR (#727). Hypothesis draws fresh seeds each
# run, so the nightly job explores inputs a per-PR run would only repeat.
[doc("property-based tests with Hypothesis (#580)")]
fuzz:
    uv run --with hypothesis --with pytest-xdist pytest -n auto --tb=short -q

# what CI runs (the full gate)
ci: setup lint typecheck test-cov audit

# fast local gate for the pre-push hook: lint + parallel tests (CI is the full
# backstop, so pre-push stays fast and doesn't re-run typecheck/coverage/audit)
[doc("fast local gate for the pre-push hook")]
fast-ci: lint test

# route CI to a self-hosted runner when Actions minutes run out (PI-666);
# requires a registered runner — see .agents/docs/guides/self-hosted-ci-runner.md
[doc("route CI to a self-hosted runner when Actions minutes run out (PI-666)")]
ci-local-on:
    gh variable set CI_RUNS_ON --body self-hosted

# back to GitHub-hosted runners
ci-local-off:
    gh variable delete CI_RUNS_ON

# scan staged changes for secrets (same scan as the pre-commit git hook)
scan:
    gitleaks git --pre-commit --staged --redact --no-banner --verbose

# regenerate .agents/docs/CODE_MAP.md (low-token "what does what" map; read before grepping)
code-map:
    uv run python .agents/scripts/gen_code_map.py


# ---------------------------------------------------------------------------
# Coursework pipeline: every command the report cites, the same on the laptop and the VM.
# Each recipe runs one `baltic <command>` in the app image (see src/baltic/__main__.py).
# ---------------------------------------------------------------------------
job := "docker compose run --rm job"

# the speed layer's ingest: Kafka (topic with ONE partition) + the producer
up:
    docker compose up -d --wait kafka
    docker compose exec -T kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --create --if-not-exists --topic gkg.raw --partitions 1
    docker compose up -d producer

# the monitor; start it after `seed`, so it resumes from the batch layer's history
monitor:
    docker compose up -d monitor

# stop the stream stack (Kafka's data stays in its volume)
down:
    docker compose down

# last closed slots and the monitor's consumer lag
status:
    tail -n 3 data/monitor/slots.jsonl || true
    docker compose exec -T kafka /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group monitor

# backfill raw files: just mirror 2026-09-01 30
mirror day days:
    {{job}} mirror {{day}} {{days}}

# Spark over the raw files into the lake: just etl 2026-09-01 30
etl day days:
    {{job}} etl {{day}} {{days}}

# Spark over the live window into lake-live (for stream = batch)
etl-live day days:
    {{job}} etl {{day}} {{days}} --live

# warm-start the monitor's detector from the lake
seed:
    {{job}} seed

# the scaling study E1-E3 (resumable): just bench 2026-09-01
bench day:
    {{job}} bench {{day}}

# AI matching through the embedding service (EMBEDDER=url; Cloud Run on GCP)
match:
    {{job}} match

# write the blind labelling sheets to data/ai/
sample:
    {{job}} sample

# score the filled-in sheets (H3, detector check)
evaluate:
    {{job}} evaluate

# check: Spark = the independent collector, day by day
reconcile:
    {{job}} reconcile

# check: the monitor's slots = Spark over the same files
stream-vs-batch:
    {{job}} stream-vs-batch

# every figure and summary.json -> data/report/
report:
    {{job}} report

# the embedding service locally
embedder:
    docker compose --profile ai up -d --wait embedder

# fault tolerance: kill the monitor, keep ingesting, restart, time the catch-up
crash-test minutes="10":
    bash infra/crash-test.sh {{minutes}}

# the test suite inside the image, where Java runs the Spark test too
test-image:
    docker compose build -q job
    docker compose run --rm --user root --entrypoint sh -v ./tests:/app/tests:ro -v ./conftest.py:/app/conftest.py:ro job -c 'uv sync --frozen --group dev -q && pytest tests -q -p no:cacheprovider'

# every guarded behaviour broken once: its test must go red
red-check:
    uv run python tests/red_check.py

# copy the VM's results (everything but raw) from the bucket: BUCKET=... just pull
pull:
    for d in lake lake-live monitor ai bench report logs; do mkdir -p data/$d && ${GCLOUD:-gcloud} storage rsync -r gs://$BUCKET/$d data/$d; done
