#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

# ===== Config (override via env) =====
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value core/project)}"
REGION="${REGION:-europe-central2}"
SERVICE_NAME="${SERVICE_NAME:-news-publisher}"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_NAME="${SA_NAME:-sa-${SERVICE_NAME}}"
SA_EMAIL="${SA_EMAIL:-${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"

# Pub/Sub topic to publish to (must exist)
TOPIC_ID="${TOPIC_ID:-news.raw}"

# Optional config for your app
GDELT_BASE="${GDELT_BASE:-https://api.gdeltproject.org}"
SOURCE_NAME="${SOURCE_NAME:-gdelt}"

# (Optional) Cloud Scheduler SA (used to invoke this service)
SCHED_SA_NAME="${SCHED_SA_NAME:-sa-scheduler}"
SCHED_SA_EMAIL="${SCHED_SA_EMAIL:-${SCHED_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"
CREATE_SCHEDULER_BINDING="${CREATE_SCHEDULER_BINDING:-true}"  # set to false to skip

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

ensure_topic() {
  if ! gcloud pubsub topics list --project "$PROJECT_ID" --format="value(name)" | grep -q "/topics/${TOPIC_ID}$"; then
    echo "Creating Pub/Sub topic: ${TOPIC_ID}"
    gcloud pubsub topics create "$TOPIC_ID" --project "$PROJECT_ID"
  else
    echo "Pub/Sub topic exists: ${TOPIC_ID}"
  fi
}

ensure_sched_sa_and_invoker() {
  # Create scheduler SA if missing
  if ! gcloud iam service-accounts list --project "$PROJECT_ID" \
       --filter="email:${SCHED_SA_EMAIL}" --format="value(email)" | grep -qx "${SCHED_SA_EMAIL}"; then
    echo "Creating scheduler SA: ${SCHED_SA_EMAIL}"
    gcloud iam service-accounts create "${SCHED_SA_NAME}" --project "${PROJECT_ID}" \
      --description="Scheduler invoker for ${SERVICE_NAME}" \
      --display-name="${SCHED_SA_NAME}"
  else
    echo "Scheduler SA exists: ${SCHED_SA_EMAIL}"
  fi

  # Grant run.invoker on this service to the scheduler SA (after first deploy we can bind; we’ll bind after deploy too)
  :
}

# ===== Enable required APIs =====
ensure_api run.googleapis.com
ensure_api cloudbuild.googleapis.com
ensure_api pubsub.googleapis.com
ensure_api cloudscheduler.googleapis.com

# ===== Ensure Pub/Sub topic =====
ensure_topic

# ===== Ensure service account + roles =====
ensure_sa
# Publisher role to publish to Pub/Sub
ensure_sa_role roles/pubsub.publisher

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
  --no-allow-unauthenticated \
  --max-instances 10 \
  --concurrency 10 \
  --set-env-vars "PROJECT_ID=${PROJECT_ID},TOPIC_ID=${TOPIC_ID},GDELT_BASE=${GDELT_BASE},SOURCE_NAME=${SOURCE_NAME}"

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"
echo "Deployed: ${SERVICE_URL}"

# ===== (Optional) prepare Scheduler SA & invoker binding =====
if [[ "${CREATE_SCHEDULER_BINDING}" == "true" ]]; then
  ensure_sched_sa_and_invoker
  echo "Granting roles/run.invoker on ${SERVICE_NAME} to ${SCHED_SA_EMAIL}"
  gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
    --project "$PROJECT_ID" --region "$REGION" \
    --member="serviceAccount:${SCHED_SA_EMAIL}" \
    --role="roles/run.invoker"
  echo "Scheduler can now call ${SERVICE_URL}/run using OIDC with audience=${SERVICE_URL}"
fi


JOB_NAME="${JOB_NAME:-${SERVICE_NAME}-hourly-run}"

ensure_scheduler_job() {
  local uri="${SERVICE_URL}/run"
  # Find a job whose httpTarget.uri matches our endpoint
  local line
  line="$(gcloud scheduler jobs list \
            --location="${REGION}" \
            --format="csv[no-heading,delimiter='|'](name,state,httpTarget.uri)" \
          | awk -F'|' -v u="$uri" '$3==u {print; exit}')"

  if [[ -n "$line" ]]; then
    local full_name state job_uri
    IFS='|' read -r full_name state job_uri <<<"$line"
    local job_id="${full_name##*/}"
    echo "Found scheduler job for ${uri}: ${job_id} (state: ${state})"
    if [[ "${state}" != "ENABLED" ]]; then
      echo "Resuming job ${job_id}"
      gcloud scheduler jobs resume "${job_id}" --location "${REGION}"
    fi
  else
    echo "Creating scheduler job ${JOB_NAME} for ${uri}"
    gcloud scheduler jobs create http "${JOB_NAME}" \
      --location "${REGION}" \
      --schedule "0 * * * *" \
      --http-method=POST \
      --uri="${uri}" \
      --headers="Content-Type=application/json" \
      --message-body='{"window_minutes":60}' \
      --oidc-service-account-email="${SCHED_SA_EMAIL}" \
      --oidc-token-audience="${SERVICE_URL}"
  fi
}

ensure_scheduler_job