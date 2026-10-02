# justfile: every command this project uses (`just --list`). Recipes are thin wrappers; the logic lives in
# the code and the tool configs.

# the locked dependencies, dev tools included
setup:
    uv sync --locked --group dev

# lint, format check, shell scripts
lint:
    uv run ruff check .
    uv run ruff format --check .
    shellcheck -S warning infra/*.sh

# auto-format
format:
    uv run ruff format .

# strict type check of the package
typecheck:
    uv run mypy src/

# the test suite (Spark's test is skipped without a JVM: `just test-image` runs it)
test:
    uv run pytest -n auto -q

# the tests with the coverage gate
test-cov:
    uv run pytest -n auto -q --cov=src --cov-fail-under=80

# known vulnerabilities in the locked dependencies
audit:
    uv run --with pip-audit pip-audit

# CI's checks (.github/workflows/ci.yml), plus the vulnerability audit
ci: setup lint typecheck test-cov red-check audit


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
    tail -n 3 ${DATA_PATH:-data}/monitor/slots.jsonl || true
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

# check: the monitor's slots = Spark over the same files (--start / --horizon: the window)
stream-vs-batch *args:
    {{job}} stream-vs-batch {{args}}

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
    docker compose run --rm --user root --entrypoint sh -v ./tests:/app/tests:ro job -c 'uv sync --frozen --group dev -q && pytest tests -q -p no:cacheprovider'

# every guarded behaviour broken once: its test must go red
red-check:
    uv run python tests/red_check.py

# copy the VM's results (everything but raw) from the bucket: BUCKET=... just pull
pull:
    for d in lake lake-live monitor ai bench report logs; do mkdir -p data/$d && ${GCLOUD:-gcloud} storage rsync -r gs://$BUCKET/$d data/$d; done
