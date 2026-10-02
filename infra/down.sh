#!/usr/bin/env bash
# After the presentation: copy the results down, then delete ONLY what infra/up.sh created.
# The project and everything else in it are never touched. Asks for the project id before deleting.
#
#   PROJECT=<id> bash infra/down.sh   # GCLOUD="<cmd>" swaps the gcloud command
set -euo pipefail
# shellcheck source=infra/env.sh
. "$(dirname "$0")/env.sh"

BUCKET=$BUCKET just pull
echo "Will delete from $PROJECT: VM ccbd-vm, Cloud Run ccbd-embedder, repository ccbd, firewall ccbd-allow-iap-ssh,"
echo "subnet ccbd-subnet, network ccbd-net, service accounts ccbd-vm + ccbd-embedder, bucket gs://$BUCKET."
read -r -p "Type the project id to confirm: " answer
[ "$answer" = "$PROJECT" ] || { echo "not confirmed, nothing deleted"; exit 1; }

# each resource: delete if it exists; a failed delete stops the script (no silent leftovers)
exists compute instances describe ccbd-vm --zone="$ZONE" && g compute instances delete ccbd-vm --zone="$ZONE"
exists run services describe ccbd-embedder --region="$REGION" && g run services delete ccbd-embedder --region="$REGION"
exists artifacts repositories describe ccbd --location="$REGION" && g artifacts repositories delete ccbd --location="$REGION"
exists compute firewall-rules describe ccbd-allow-iap-ssh && g compute firewall-rules delete ccbd-allow-iap-ssh
exists compute networks subnets describe ccbd-subnet --region="$REGION" && g compute networks subnets delete ccbd-subnet --region="$REGION"
exists compute networks describe ccbd-net && g compute networks delete ccbd-net
for sa in ccbd-vm ccbd-embedder; do
  exists iam service-accounts describe "$sa@$PROJECT.iam.gserviceaccount.com" &&
    g iam service-accounts delete "$sa@$PROJECT.iam.gserviceaccount.com"
done
exists storage buckets describe "gs://$BUCKET" && g storage rm -r "gs://$BUCKET"

echo "residue check (should print nothing):"
g compute instances list --filter="labels.purpose=ccbd-coursework" --format="value(name)"
g run services list --region="$REGION" --filter="metadata.name=ccbd-embedder" --format="value(metadata.name)"
g iam service-accounts list --filter="email~^ccbd-" --format="value(email)"
g storage buckets list --filter="name=$BUCKET" --format="value(name)"
