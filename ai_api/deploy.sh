#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
# ============================================================
# Deploy AI API to Cloud Run via Cloud Build + Artifact Registry
# - Builds Dockerfile at ./ai_api/Dockerfile
# - Pushes to Artifact Registry
# - Deploys to Cloud Run using the specified service account
#
# Usage:
#   ./deploy_ai_api.sh [--project PROJECT_ID] [--region REGION] \
#     [--service SERVICE_NAME] [--repo REPOSITORY_NAME] [--unauth]
#
# Defaults:
#   PROJECT_ID: from `gcloud config get-value project`
#   REGION: europe-central2
#   SERVICE: ai-api
#   REPO: containers
#   AUTH: authenticated (omit --unauth to keep it private)
#
# Examples:
#   ./deploy_ai_api.sh
#   ./deploy_ai_api.sh --project my-proj --region europe-west1 --service ai-api --repo app-images --unauth
# ============================================================

# ---------- Configurable defaults ----------
PROJECT_ID_DEFAULT="$(gcloud config get-value project 2>/dev/null || true)"
REGION_DEFAULT="europe-central2"         # Warsaw (reasonable for Europe/Sofia)
SERVICE_DEFAULT="ai-api"
REPO_DEFAULT="containers"               # Artifact Registry repo name
UNAUTH_DEFAULT="false"

# ---------- Parse args ----------
PROJECT_ID="${PROJECT_ID_DEFAULT}"
REGION="${REGION_DEFAULT}"
SERVICE="${SERVICE_DEFAULT}"
REPO="${REPO_DEFAULT}"
UNAUTH="${UNAUTH_DEFAULT}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --unauth) UNAUTH="true"; shift 1 ;;
    -h|--help)
      grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "ERROR: No PROJECT_ID provided and none set in gcloud config." >&2
  echo "Provide with --project or run: gcloud config set project YOUR_PROJECT_ID" >&2
  exit 1
fi

# ---------- Pre-flight: show plan ----------
echo "-------------------------------------------"
echo "Project:        ${PROJECT_ID}"
echo "Region:         ${REGION}"
echo "Service:        ${SERVICE}"
echo "Repository:     ${REPO}"
echo "Unauth access:  ${UNAUTH}"
echo "Build context:  ./ai_api"
echo "-------------------------------------------"

# ---------- Enable required APIs ----------
echo "Ensuring required APIs are enabled..."
gcloud services enable \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  --project "${PROJECT_ID}" >/dev/null

# ---------- Ensure Artifact Registry repo exists ----------
echo "Checking Artifact Registry repository '${REPO}' in ${REGION}..."
if ! gcloud artifacts repositories describe "${REPO}" \
    --location "${REGION}" \
    --project "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Creating Artifact Registry repository '${REPO}'..."
  gcloud artifacts repositories create "${REPO}" \
    --repository-format=docker \
    --location "${REGION}" \
    --description="Docker images for Cloud Run services" \
    --project "${PROJECT_ID}"
else
  echo "Repository '${REPO}' exists."
fi

# ---------- Build & push image via Cloud Build ----------
# Tag: prefer git SHA; fallback to date-time
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  GIT_SHA="$(git rev-parse --short HEAD)"
  TAG="${GIT_SHA}"
else
  TAG="$(date +%Y%m%d-%H%M%S)"
fi

IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${SERVICE}:${TAG}"
echo "Building and pushing image: ${IMAGE_URI}"

# Ensure Dockerfile path exists
if [[ ! -f "./Dockerfile" ]]; then
  echo "ERROR: ./Dockerfile not found." >&2
  exit 1
fi

gcloud builds submit . \
  --tag "${IMAGE_URI}" \
  --project "${PROJECT_ID}"

# ---------- Deploy to Cloud Run ----------
echo "Deploying to Cloud Run service '${SERVICE}' in ${REGION}..."
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE_URI}" \
  --region "${REGION}" \
  --platform managed \
  --project "${PROJECT_ID}" \
  --service-account "ai-api@world-monitor.iam.gserviceaccount.com" \
  --update-secrets=OPENAI_API_KEY=OPENAI_API_KEY:latest \
  --quiet

# ---------- Configure unauthenticated access (optional) ----------
if [[ "${UNAUTH}" == "true" ]]; then
  echo "Granting unauthenticated (public) access..."
  gcloud run services add-iam-policy-binding "${SERVICE}" \
    --region "${REGION}" \
    --project "${PROJECT_ID}" \
    --member="allUsers" \
    --role="roles/run.invoker" \
    --quiet
fi

# ---------- Output the service URL ----------
SERVICE_URL="$(gcloud run services describe "${SERVICE}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --format='value(status.url)')"

echo "-------------------------------------------"
echo "Deployed image: ${IMAGE_URI}"
echo "Service URL:    ${SERVICE_URL}"
echo "Service acct:   ai-api@world-monitor.iam.gserviceaccount.com"
echo "Done."
