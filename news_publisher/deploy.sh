#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value core/project)}"
REGION="${REGION:-europe-central2}"
SERVICE_NAME="news-publisher"
SCHED_SA_NAME="${SCHED_SA_NAME:-sa-scheduler}"
SCHED_SA_EMAIL="${SCHED_SA_EMAIL:-${SCHED_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"
SCHED_JOB_NAME="${SCHED_JOB_NAME:-${SERVICE_NAME}-hourly-trigger}"

# Ensure executable
chmod +x ../deploy_service.sh

# ../deploy_service.sh \
#   --project "$PROJECT_ID" \
#   --region "$REGION" \
#   --service "$SERVICE_NAME" \
#   --build-context "./news_publisher" \
#   --roles "roles/pubsub.publisher" \
#   --extra-apis "run.googleapis.com,cloudbuild.googleapis.com,pubsub.googleapis.com,cloudscheduler.googleapis.com" \
#   --env "PROJECT_ID=${PROJECT_ID},TOPIC_ID=news.raw,GDELT_BASE=https://api.gdeltproject.org,SOURCE_NAME=gdelt" \
#   --max-instances 10 \
#   --concurrency 10 \
#   --no-allow-unauth

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')"

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

ensure_sched_sa_and_invoker
echo "Granting roles/run.invoker on ${SERVICE_NAME} to ${SCHED_SA_EMAIL}"
gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
  --project "$PROJECT_ID" --region "$REGION" \
  --member="serviceAccount:${SCHED_SA_EMAIL}" \
  --role="roles/run.invoker"
echo "Scheduler can now call ${SERVICE_URL}/run using OIDC with audience=${SERVICE_URL}"


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
    echo "Creating scheduler job ${SCHED_JOB_NAME} for ${uri}"
    gcloud scheduler jobs create http "${SCHED_JOB_NAME}" \
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
