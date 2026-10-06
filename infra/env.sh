# shellcheck shell=bash
# Sourced by up.sh and down.sh: the names both must agree on, and the gcloud wrapper.
: "${PROJECT:?set PROJECT}"
REGION=${REGION:-europe-north1}
ZONE=${ZONE:-europe-north1-a}
BUCKET=${BUCKET:-$PROJECT-ccbd-baltic}
read -r -a G <<< "${GCLOUD:-gcloud}"
g() { "${G[@]}" --project "$PROJECT" --quiet "$@"; }
exists() { g "$@" >/dev/null 2>&1; }
