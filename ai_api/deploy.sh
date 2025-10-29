#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value core/project)}"
REGION="${REGION:-europe-central2}"

# Ensure executable
chmod +x ../deploy_service.sh

# 1) ai-api
../deploy_service.sh \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --service "ai-api" \
  --build-context "./ai_api" \
  --extra-apis "artifactregistry.googleapis.com,cloudbuild.googleapis.com,run.googleapis.com,secretmanager.googleapis.com,aiplatform.googleapis.com" \
  --no-allow-unauth \
  --secrets "OPENAI_API_KEY=OPENAI_API_KEY:latest"  # Uncomment for OpenAI support, requires the key to be in Secret Manager
