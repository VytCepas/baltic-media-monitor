#!/usr/bin/env bash
# ccbd-vm's startup script: runs as root on EVERY boot. Each step skips itself once its output exists,
# so a reboot (or the Oct 5 stop/start) continues where the last boot stopped.
# Log: /var/log/ccbd.log, copied to gs://<bucket>/logs/ccbd.log every 5 minutes.
set -uo pipefail
exec >>/var/log/ccbd.log 2>&1
md() { curl -fsH 'Metadata-Flavor: Google' "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1"; }
BUCKET=$(md ccbd-bucket) || { echo "no ccbd-bucket metadata"; exit 1; }
# gcloud caches "not on GCE" in this file when its first call races the network at boot, and then
# refuses every later call ("no active account"): clear it on every boot.
rm -f /root/.config/gcloud/gce
EMBEDDER=$(md ccbd-embedder-url)
export EMBEDDER
log() { echo "$(date -u +%FT%TZ) $*"; }
retry() { for i in 1 2 3 4 5; do "$@" && return 0; log "retry $i: $*"; sleep $((30 * i)); done; return 1; }
up() { gcloud storage rsync -r "data/$1" "gs://$BUCKET/$1" --quiet; }  # the bucket mirrors data/
CORES=$(nproc)
log "boot, $CORES vCPU"

# 1. host: swap, Docker, just (first boot only)
[ -f /swapfile ] || { fallocate -l 8G /swapfile && chmod 600 /swapfile && mkswap /swapfile; }
swapon /swapfile 2>/dev/null
command -v docker >/dev/null || retry sh -c 'curl -fsSL https://get.docker.com | sh'
JUST=https://github.com/casey/just/releases/download/1.56.0/just-1.56.0-x86_64-unknown-linux-musl.tar.gz
JUST_SHA=fa2a8ec1015d9df5330941ade12437488fc40d33f9c9f8cd4eb70a26de11b639  # the release's SHA256SUMS
command -v just >/dev/null || { retry curl -fsSLo /tmp/just.tgz "$JUST" &&
  echo "$JUST_SHA  /tmp/just.tgz" | sha256sum -c - && tar xzf /tmp/just.tgz -C /usr/local/bin just; } || exit 1

# 2. code and data
mkdir -p /opt/baltic && cd /opt/baltic || exit 1
{ retry gcloud storage cp "gs://$BUCKET/code/baltic.tgz" /tmp/baltic.tgz && tar xzf /tmp/baltic.tgz; } || exit 1
# a new disk on a used bucket: restore raw/ (on the very first boot the bucket has none yet)
if [ ! -d data/raw ] && gcloud storage ls "gs://$BUCKET/raw/" >/dev/null 2>&1; then
  retry gcloud storage rsync -r "gs://$BUCKET/raw" data/raw
fi
mkdir -p data && chown -R 1000:1000 data  # the app image runs as uid 1000
printf 'SINCE=20261001000000\nSPARK_MEM=%s\n' "$([ "$CORES" -ge 8 ] && echo 14g || echo 2g)" > .env
retry docker compose build -q

# 3. speed layer: Kafka + the producer, catching up from Oct 1 (the monitor waits for its seed)
retry just up
# recurring job "ccbd-sync": every 5 min, as root on this VM, copy monitor/ and the log to the bucket.
# A transient systemd timer: it ends with the VM and is re-created on each boot (stop: systemctl stop ccbd-sync.timer)
systemctl is-active --quiet ccbd-sync.timer || systemd-run --unit=ccbd-sync --on-active=5min --on-unit-active=5min \
  sh -c "gcloud storage rsync -r /opt/baltic/data/monitor gs://$BUCKET/monitor --quiet; gcloud storage cp /var/log/ccbd.log gs://$BUCKET/logs/ccbd.log --quiet"

# 4. batch layer: September, then hand the history to the speed layer
retry just mirror 2026-09-01 30 && up raw
[ -f data/lake/gold/series_15m/_SUCCESS ] || { log "Spark ETL, 30 days"; just etl 2026-09-01 30 && up lake; }
just seed && just monitor

# 5. AI: match through the private Cloud Run service, then the labelling sheets
[ -f data/ai/pairs.parquet ] || { log "AI matching via $EMBEDDER"; retry just match; }
[ -f data/ai/labels_pairs.csv ] || just sample
up ai

# 6. the scaling study, only on the big machine (resumable)
if [ "$CORES" -ge 8 ] && [ ! -f data/bench/.done ]; then
  log "scaling study"
  just bench 2026-09-01 && touch data/bench/.done
  up bench
fi

# 7. every boot: Spark over the live window, then stream = batch up to now - 30 min
DAYS=$(( ($(date -u +%s) - $(date -u -d 2026-10-01 +%s)) / 86400 + 1 ))
nice just etl-live 2026-10-01 "$DAYS" && just stream-vs-batch
just report
for d in raw lake-live monitor report; do up "$d"; done
log "batch pipeline done"
