#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value core/project)}"
REGION="${REGION:-europe-central2}"

# Ensure executable
chmod +x ../deploy_service.sh

../deploy_service.sh \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --service "news-atlas" \
  --build-context "./news_atlas" \
  --roles "roles/datastore.user" \
  --extra-apis "run.googleapis.com,cloudbuild.googleapis.com,firestore.googleapis.com" \
  --env "PROJECT_ID=${PROJECT_ID}" \
  --max-instances 10 \
  --concurrency 10 \
  --allow-unauth
