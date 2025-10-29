#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value core/project)}"
REGION="${REGION:-europe-central2}"
FIRESTORE_DB_NAME="world-news-knowledge"

# Ensure executable
chmod +x ../deploy_service.sh

AI_API_DEFAULT="https://ai-api-1010480476071.europe-central2.run.app"
../deploy_service.sh \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --service "news-processor" \
  --build-context "./news_processor" \
  --roles "roles/datastore.user" \
  --extra-apis "run.googleapis.com,cloudbuild.googleapis.com,firestore.googleapis.com,secretmanager.googleapis.com" \
  --env "PROJECT_ID=${PROJECT_ID},AI_API=${AI_API_DEFAULT}" \
  --max-instances 50 \
  --concurrency 4 \
  --no-allow-unauth


if ! gcloud firestore databases describe "$FIRESTORE_DB_NAME" --project "$PROJECT_ID" >/dev/null 2>&1; then
echo "Creating Firestore database: $FIRESTORE_DB_NAME"
gcloud firestore databases create \
  --project "$PROJECT_ID" \
  --database="$FIRESTORE_DB_NAME" \
  --location="europe-central2" \
  --type="firestore-native"
else
  echo "Firestore database '$FIRESTORE_DB_NAME' already exists."
fi
