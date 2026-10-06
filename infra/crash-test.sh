#!/usr/bin/env bash
# Fault tolerance: kill -9 the monitor, keep ingesting for N minutes, restart it, and time the catch-up.
# Kafka buffers what the monitor missed; at-least-once + the saved state mean nothing is lost, and a
# slot closed twice (crash between writing it and saving state) shows up as a counted duplicate line
# (afterwards: `just etl-live ...` and `just stream-vs-batch` prove both).   usage: crash-test.sh MINUTES
#
# "Caught up" = the monitor closed the newest slot archived when it restarted. Kafka's committed lag is
# not the measure: the monitor commits only while no slot is half-read, so it rarely shows exactly 0.
set -euo pipefail
data=${DATA_PATH:-./data}
newest() { find "$data/raw/feed=tr" -name '*.zip' | sed 's|.*/||; s|\.zip$||' | sort | tail -1; }
docker compose kill -s KILL monitor >/dev/null
echo "$(date -u +%T) monitor killed; ingesting without it for $1 min"
sleep $(($1 * 60))
target=$(newest)
start=$(date +%s)
docker compose start monitor >/dev/null
echo "$(date -u +%T) restarted; waiting for slot $target"
until grep -q "\"slot\": \"$target\"" "$data/monitor/slots.jsonl"; do sleep 1; done
echo "$(date -u +%T) caught up in $(($(date +%s) - start)) s"
