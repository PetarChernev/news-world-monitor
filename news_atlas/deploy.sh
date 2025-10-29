#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

# ===== Config (override via env) =====
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value core/project)}"
REGION="${REGION:-europe-central2}"
SERVICE_NAME="${SERVICE_NAME:-news-atlas}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_NAME="${SA_NAME:-sa-${SERVICE_NAME}}"
SA_EMAIL="${SA_EMAIL:-${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"
echo $SA_EMAIL
# ===== Helpers =====
ensure_api() {
  local api="$1"
  gcloud services enable "$api" --project "$PROJECT_ID" >/dev/null
}

sa_exists() {
  gcloud iam service-accounts list \
    --project "$PROJECT_ID" \
    --filter="email:${SA_EMAIL}" \
    --format="value(email)" | grep -qx "${SA_EMAIL}" || return 1
}

ensure_sa() {
  if ! sa_exists; then
    echo "Creating service account: ${SA_EMAIL}"
    gcloud iam service-accounts create "${SA_NAME}" --project "${PROJECT_ID}" \
      --description="Service account for ${SERVICE_NAME}" \
      --display-name="${SERVICE_NAME}"
  else
    echo "Service account exists: ${SA_EMAIL}"
  fi
}

ensure_sa_role() {
  local role="$1"
  if ! gcloud projects get-iam-policy "$PROJECT_ID" \
      --flatten="bindings[].members" \
      --filter="bindings.members:serviceAccount:${SA_EMAIL} AND bindings.role:${role}" \
      --format="value(bindings.role)" | grep -q "${role}"; then
    echo "Granting role ${role} to ${SA_EMAIL}"
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="${role}" >/dev/null
  else
    echo "Role ${role} already granted to ${SA_EMAIL}"
  fi
}


# ===== Enable required APIs =====
ensure_api run.googleapis.com
ensure_api cloudbuild.googleapis.com
ensure_api firestore.googleapis.com


# ===== Ensure service account + roles =====
ensure_sa
ensure_sa_role roles/datastore.user

# ===== Build image =====
echo "Building image ${IMAGE} ..."
gcloud builds submit --project "$PROJECT_ID" --tag "$IMAGE" .

# ===== Deploy to Cloud Run =====
echo "Deploying ${SERVICE_NAME} to Cloud Run ..."
gcloud run deploy "$SERVICE_NAME" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$IMAGE" \
  --service-account "$SA_EMAIL" \
  --max-instances 10 \
  --concurrency 10 \
  --set-env-vars "PROJECT_ID=${PROJECT_ID}"

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
echo "Deployed: ${SERVICE_URL}"
