#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

# ============================================================
# Generic Cloud Run deploy script (argument-based)
#
# Usage example:
#   ./deploy_service.sh \
#     --project my-proj \
#     --region europe-central2 \
#     --service news-processor \
#     --build-context . \
#     --roles roles/datastore.user \
#     --extra-apis firestore.googleapis.com,secretmanager.googleapis.com \
#     --env PROJECT_ID=my-proj,AI_API=https://my-api.run.app \
#     --max-instances 50 \
#     --concurrency 4 \
#     --no-allow-unauth
# ============================================================

# -------- Defaults --------
PROJECT_ID=""
REGION="europe-central2"
SERVICE_NAME=""
BUILD_CONTEXT="."
ROLES=""
EXTRA_APIS=""
ENV_VARS=""
SECRETS=""
ALLOW_UNAUTH="false"
MAX_INSTANCES="10"
CONCURRENCY="10"

# -------- Parse arguments --------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) PROJECT_ID="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --service) SERVICE_NAME="$2"; shift 2 ;;
    --build-context) BUILD_CONTEXT="$2"; shift 2 ;;
    --roles) ROLES="$2"; shift 2 ;;
    --extra-apis) EXTRA_APIS="$2"; shift 2 ;;
    --env) ENV_VARS="$2"; shift 2 ;;
    --secrets) SECRETS="$2"; shift 2 ;;
    --allow-unauth) ALLOW_UNAUTH="true"; shift 1 ;;
    --no-allow-unauth) ALLOW_UNAUTH="false"; shift 1 ;;
    --max-instances) MAX_INSTANCES="$2"; shift 2 ;;
    --concurrency) CONCURRENCY="$2"; shift 2 ;;
    -h|--help)
      grep -E '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "ERROR: --project is required or must be set in gcloud config." >&2
  exit 1
fi
if [[ -z "${SERVICE_NAME}" ]]; then
  echo "ERROR: --service is required." >&2
  exit 1
fi

IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"
SA_NAME="sa-${SERVICE_NAME}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# -------- Helpers --------
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
    gcloud iam service-accounts create "${SA_NAME}" \
      --project "${PROJECT_ID}" \
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

# Ensure a member has a role on the project (idempotent)
ensure_project_binding() {
  local member="$1"
  local role="$2"
  if ! gcloud projects get-iam-policy "$PROJECT_ID" \
      --flatten="bindings[].members" \
      --filter="bindings.members:${member} AND bindings.role:${role}" \
      --format="value(bindings.role)" | grep -q "${role}"; then
    echo "Granting ${role} to ${member}"
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="${member}" \
      --role="${role}" >/dev/null
  else
    echo "Binding already present: ${member} has ${role}"
  fi
}

# For each secret provided via --secrets, grant Cloud Build SA secret accessor
grant_cb_sa_secret_access_for_secrets() {
  [[ -z "${SECRETS}" ]] && return 0

  # Resolve project number to build the Cloud Build SA email
  local project_number
  project_number="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
  if [[ -z "${project_number}" ]]; then
    echo "ERROR: Could not determine project number for ${PROJECT_ID}" >&2
    exit 1
  fi
  local cb_sa="serviceAccount:${project_number}@cloudbuild.gserviceaccount.com"
  local role="roles/secretmanager.secretAccessor"

  # Parse secret specs like "ENV=secretName:version"
  IFS=',' read -r -a specs <<< "${SECRETS}"
  declare -A uniq_secret_names=()
  for spec in "${specs[@]}"; do
    [[ -z "${spec}" ]] && continue
    # Split "ENV=secret[:version]"
    local right
    right="${spec#*=}"
    # Extract secret name before optional colon
    local secret_name="${right%%:*}"
    [[ -z "${secret_name}" ]] && continue
    uniq_secret_names["${secret_name}"]=1
  done

  # Grant binding once (project-level) — we run per secret as requested, but deduplicate
  for s in "${!uniq_secret_names[@]}"; do
    echo "Ensuring Cloud Build SA can access secret '${s}' (project-level accessor role)"
    ensure_project_binding "${cb_sa}" "${role}"
  done
}

# -------- Show plan --------
echo "-------------------------------------------"
echo "Project:        ${PROJECT_ID}"
echo "Region:         ${REGION}"
echo "Service:        ${SERVICE_NAME}"
echo "Build context:  ${BUILD_CONTEXT}"
echo "Image:          ${IMAGE}"
echo "Roles:          ${ROLES:-<none>}"
echo "Extra APIs:     ${EXTRA_APIS:-<none>}"
echo "Env vars:       ${ENV_VARS:-<none>}"
echo "Secrets:        ${SECRETS:-<none>}"
echo "Auth:           ${ALLOW_UNAUTH}"
echo "-------------------------------------------"

# -------- Enable APIs --------
ensure_api run.googleapis.com
ensure_api cloudbuild.googleapis.com
if [[ -n "${EXTRA_APIS}" ]]; then
  IFS=',' read -r -a apis <<<"${EXTRA_APIS}"
  for api in "${apis[@]}"; do
    [[ -n "$api" ]] && ensure_api "$api"
  done
fi

# -------- SA + roles --------
ensure_sa
if [[ -n "${ROLES}" ]]; then
  IFS=',' read -r -a roles <<<"${ROLES}"
  for role in "${roles[@]}"; do
    [[ -n "$role" ]] && ensure_sa_role "$role"
  done
fi

# -------- Cloud Build SA secret accessor (per provided secret) --------
grant_cb_sa_secret_access_for_secrets

# -------- Build image --------
echo "Building image ${IMAGE} ..."
gcloud builds submit "${BUILD_CONTEXT}" --project "$PROJECT_ID" --tag "$IMAGE"

# -------- Deploy --------
echo "Deploying ${SERVICE_NAME} ..."
args=(
  gcloud run deploy "$SERVICE_NAME"
  --project "$PROJECT_ID"
  --region "$REGION"
  --image "$IMAGE"
  --service-account "$SA_EMAIL"
  --max-instances "$MAX_INSTANCES"
  --concurrency "$CONCURRENCY"
)
if [[ "${ALLOW_UNAUTH}" == "true" ]]; then
  args+=(--allow-unauthenticated)
else
  args+=(--no-allow-unauthenticated)
fi
[[ -n "${ENV_VARS}" ]] && args+=(--set-env-vars "${ENV_VARS}")
[[ -n "${SECRETS}" ]] && args+=(--update-secrets "${SECRETS}")

"${args[@]}"

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" \
  --project "$PROJECT_ID" --region "$REGION" \
  --format='value(status.url)')"

echo "Deployed: ${SERVICE_URL}"
