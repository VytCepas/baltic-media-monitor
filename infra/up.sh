#!/usr/bin/env bash
# Create (or update) the whole deployment in a SHARED project, run once by the operator from the laptop.
# Only isolated resources named ccbd-* and labelled purpose=ccbd-coursework; nothing project-wide changes.
# Idempotent: what exists is kept, and a rerun ships the current commit and the current startup script.
#
#   PROJECT=<id> INVOKER=user:<you@example.com> bash infra/up.sh   # GCLOUD="<cmd>" swaps the gcloud command
set -euo pipefail
# shellcheck source=infra/env.sh
. "$(dirname "$0")/env.sh"
MACHINE=${MACHINE:-e2-standard-8}        # big for the backfill and the study, then downsized
STOP_AT=${STOP_AT:-2026-10-08T00:00:00+03:00}
LABELS=purpose=ccbd-coursework
VM_SA=ccbd-vm@$PROJECT.iam.gserviceaccount.com
AI_SA=ccbd-embedder@$PROJECT.iam.gserviceaccount.com
IMAGE=$REGION-docker.pkg.dev/$PROJECT/ccbd/embedder:v1
step() { echo "== $*"; }

step "storage: private bucket, raw/ expires after 30 days"
exists storage buckets describe "gs://$BUCKET" ||
  g storage buckets create "gs://$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
g storage buckets update "gs://$BUCKET" --update-labels="$LABELS" --lifecycle-file=infra/lifecycle.json

step "identities: the VM may use this bucket only; the AI service has no roles"
exists iam service-accounts describe "$VM_SA" || g iam service-accounts create ccbd-vm --display-name="ccbd coursework VM"
exists iam service-accounts describe "$AI_SA" || g iam service-accounts create ccbd-embedder --display-name="ccbd embedder, no roles"
for i in 1 2 3 4 5; do  # a new service account takes a while to be visible to IAM
  g storage buckets add-iam-policy-binding "gs://$BUCKET" --member="serviceAccount:$VM_SA" \
    --role=roles/storage.objectAdmin >/dev/null && break
  [ "$i" = 5 ] && exit 1
  sleep 15
done

step "AI image: Cloud Build (default build identity), staging and logs kept in our bucket"
exists artifacts repositories describe ccbd --location="$REGION" ||
  g artifacts repositories create ccbd --repository-format=docker --location="$REGION" --labels="$LABELS"
exists artifacts docker images describe "$IMAGE" ||
  g builds submit ai/embedder --tag="$IMAGE" --region="$REGION" --machine-type=e2-highcpu-8 \
    --gcs-source-staging-dir="gs://$BUCKET/build/source" --gcs-log-dir="gs://$BUCKET/build/logs" || {
    echo "Cloud Build failed. Fallback: docker buildx build --platform linux/amd64 -t $IMAGE --push ai/embedder"
    exit 1
  }

step "AI service: private Cloud Run, scales to zero, at most 2 instances"
g run deploy ccbd-embedder --region="$REGION" --image="$IMAGE" --service-account="$AI_SA" \
  --no-allow-unauthenticated --cpu=2 --memory=2Gi --concurrency=4 --min-instances=0 --max-instances=2 \
  --timeout=300 --labels="$LABELS" >/dev/null
for member in "serviceAccount:$VM_SA" ${INVOKER:+"$INVOKER"}; do
  g run services add-iam-policy-binding ccbd-embedder --region="$REGION" --member="$member" \
    --role=roles/run.invoker >/dev/null
done
URL=$(g run services describe ccbd-embedder --region="$REGION" --format='value(status.url)')

step "network: own VPC; SSH only from Identity-Aware Proxy"
exists compute networks describe ccbd-net || g compute networks create ccbd-net --subnet-mode=custom
exists compute networks subnets describe ccbd-subnet --region="$REGION" ||
  g compute networks subnets create ccbd-subnet --network=ccbd-net --region="$REGION" --range=10.90.0.0/24
exists compute firewall-rules describe ccbd-allow-iap-ssh ||
  g compute firewall-rules create ccbd-allow-iap-ssh --network=ccbd-net --direction=INGRESS --action=allow \
    --rules=tcp:22 --source-ranges=35.235.240.0/20

step "code: this commit as a tarball the VM pulls on every boot"
CODE=$(mktemp) && git archive --format=tar.gz -o "$CODE" HEAD
g storage cp "$CODE" "gs://$BUCKET/code/baltic.tgz" && rm -f "$CODE"

step "VM: $MACHINE, pd-balanced 80 GB, stops itself at $STOP_AT"
META="enable-oslogin=TRUE,ccbd-bucket=$BUCKET,ccbd-embedder-url=$URL"
if exists compute instances describe ccbd-vm --zone="$ZONE"; then
  g compute instances add-metadata ccbd-vm --zone="$ZONE" --metadata="$META" \
    --metadata-from-file=startup-script=infra/vm.sh
else
  g compute instances create ccbd-vm --zone="$ZONE" --machine-type="$MACHINE" \
    --network=ccbd-net --subnet=ccbd-subnet --image-family=debian-12 --image-project=debian-cloud \
    --boot-disk-size=80GB --boot-disk-type=pd-balanced --labels="$LABELS" \
    --service-account="$VM_SA" --scopes=cloud-platform --shielded-secure-boot \
    --termination-time="$STOP_AT" --instance-termination-action=STOP \
    --metadata="$META" --metadata-from-file=startup-script=infra/vm.sh
fi
echo "done. Log: gcloud storage cat gs://$BUCKET/logs/ccbd.log | tail"
