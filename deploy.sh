#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")"

# ============================================================
# Root-level deployment orchestrator
# Iterates through service directories and runs their deploy.sh
#
# Expected structure:
#   ./ai-api/deploy.sh
#   ./news-processor/deploy.sh
#   ./news-publisher/deploy.sh
#   ./news-atlas/deploy.sh
# ============================================================

SERVICES=(
  "ai_api"
  "news_processor"
  "news_publisher"
  "news_atlas"
)

for dir in "${SERVICES[@]}"; do
  SCRIPT="${dir}/deploy.sh"
  echo "-------------------------------------------"
  echo "Deploying service: ${dir}"
  echo "-------------------------------------------"

  if [[ ! -f "${SCRIPT}" ]]; then
    echo "❌ ERROR: ${SCRIPT} not found. Skipping."
    exit 1
  fi

  chmod +x "${SCRIPT}"
  (cd "${dir}" && ./deploy.sh)

  echo "✅ ${dir} deployed successfully."
  echo
done

echo "🎉 All services deployed successfully (ai-api → news-processor → news-publisher → news-atlas)"
