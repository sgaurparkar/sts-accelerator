#!/usr/bin/env bash
# One-time ADMIN setup for multi_project mode. Run in Cloud Shell as a user who is Organization/Folder admin
# and Billing admin. Safe to re-run.
#
#   ORG_ID=123456789012 BILLING_ID=012345-ABCDEF-678901 bash scripts/gcp_multi_project_setup.sh
#
# What it does: creates the folder, grants the runner service account its roles ON THE FOLDER
# (projects created inside inherit them), and lets it link billing. Prints the folder id for settings.yaml.
set -euo pipefail
: "${ORG_ID:?set ORG_ID (gcloud organizations list)}"
: "${BILLING_ID:?set BILLING_ID (gcloud billing accounts list)}"
CONN_PROJECT="${CONN_PROJECT:-network-connectivity-511917}"
RUNNER_SA="${RUNNER_SA:-sts-runner-sa@${CONN_PROJECT}.iam.gserviceaccount.com}"
FOLDER_NAME="${FOLDER_NAME:-azure-migration}"

FOLDER_ID="$(gcloud resource-manager folders list --organization="$ORG_ID" \
  --filter="displayName=$FOLDER_NAME" --format='value(name)' | head -1 | sed 's#folders/##')"
if [ -z "$FOLDER_ID" ]; then
  gcloud resource-manager folders create --display-name="$FOLDER_NAME" --organization="$ORG_ID"
  FOLDER_ID="$(gcloud resource-manager folders list --organization="$ORG_ID" \
    --filter="displayName=$FOLDER_NAME" --format='value(name)' | head -1 | sed 's#folders/##')"
fi
echo "Folder: $FOLDER_NAME  id=$FOLDER_ID"

for ROLE in roles/resourcemanager.projectCreator roles/serviceusage.serviceUsageAdmin \
            roles/bigquery.jobUser roles/bigquery.dataEditor roles/storage.admin; do
  gcloud resource-manager folders add-iam-policy-binding "$FOLDER_ID" \
    --member="serviceAccount:$RUNNER_SA" --role="$ROLE" --condition=None --quiet >/dev/null
  echo "  granted $ROLE on folder"
done
gcloud billing accounts add-iam-policy-binding "$BILLING_ID" \
  --member="serviceAccount:$RUNNER_SA" --role=roles/billing.user --quiet >/dev/null
echo "  granted roles/billing.user on billing account $BILLING_ID"

# The runner (project $CONN_PROJECT) also needs its own API on: the quota/billing project for calls.
gcloud services enable cloudresourcemanager.googleapis.com cloudbilling.googleapis.com serviceusage.googleapis.com \
  --project="$CONN_PROJECT"

cat <<EOT

Put this in config/settings.yaml on the runner:
multi_project:
  enabled: true
  folder_id: "$FOLDER_ID"
  billing_account: "$BILLING_ID"
EOT
