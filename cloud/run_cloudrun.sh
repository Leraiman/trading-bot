#!/usr/bin/env bash
set -euo pipefail

: "${PROJECT_ID:?PROJECT_ID required}"
: "${REGION:?REGION required}"
: "${SERVICE_NAME:?SERVICE_NAME required}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

SA_NAME="${SERVICE_NAME}-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE_URI="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:${IMAGE_TAG}"

echo ">>> gcloud project: ${PROJECT_ID} | region: ${REGION} | service: ${SERVICE_NAME} | image: ${IMAGE_URI}"

gcloud config set project "${PROJECT_ID}"

# Enable APIs
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com

# Create Service Account if not exists
if ! gcloud iam service-accounts describe "${SA_EMAIL}" >/dev/null 2>&1; then
  echo ">>> Creating Service Account ${SA_EMAIL}"
  gcloud iam service-accounts create "${SA_NAME}" --display-name "${SERVICE_NAME} SA"
fi

# IAM roles for deployer (current user) to use SA
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --member="user:$(gcloud config get-value account)" \
  --role="roles/iam.serviceAccountUser" >/dev/null

# Grant runtime roles to SA
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor" >/dev/null
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/logging.logWriter" >/dev/null
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/monitoring.metricWriter" >/dev/null

# Create/update secrets (only creates empty versions; set values afterward or via CLI)
create_secret() {
  local name="$1"
  if ! gcloud secrets describe "$name" >/dev/null 2>&1; then
    echo ">>> Creating secret $name"
    gcloud secrets create "$name" --replication-policy="automatic"
    echo -n "" | gcloud secrets versions add "$name" --data-file=-
  fi
}

create_secret "BINANCE_READONLY_KEY"
create_secret "BINANCE_READONLY_SECRET"
create_secret "BINANCE_TRADE_KEY"
create_secret "BINANCE_TRADE_SECRET"

# Build image
echo ">>> Building container"
gcloud builds submit --tag "${IMAGE_URI}" .

# Deploy to Cloud Run
echo ">>> Deploying to Cloud Run"
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE_URI}" \
  --region "${REGION}" \
  --platform managed \
  --service-account "${SA_EMAIL}" \
  --port 8080 \
  --allow-unauthenticated \
  --cpu 1 \
  --memory 512Mi \
  --concurrency 10 \
  --min-instances 0 \
  --max-instances 2 \
  --set-env-vars APP_ENV=prod,APP_NAME=${SERVICE_NAME},APP_VERSION=${IMAGE_TAG},LOG_LEVEL=INFO,METRICS_NAMESPACE=trading_bot,MARKET=spot,SYMBOLS=BTCUSDT,INTERVAL=1h,CAPITAL_BASE_USD=10000,FEE_BPS=10,SLIPPAGE_BPS=5,LATENCY_MS=150,RISK_PER_TRADE_BPS=50,DAILY_LOSS_CAP_BPS=200,MAX_DRAWDOWN_BPS=1000,ALLOW_LEVERAGE=false,MAX_LEVERAGE=1 \
  --set-secrets BINANCE_READONLY_KEY=BINANCE_READONLY_KEY:latest,BINANCE_READONLY_SECRET=BINANCE_READONLY_SECRET:latest,BINANCE_TRADE_KEY=BINANCE_TRADE_KEY:latest,BINANCE_TRADE_SECRET=BINANCE_TRADE_SECRET:latest

echo ">>> Deployed. URL:"
gcloud run services describe "${SERVICE_NAME}" --region "${REGION}" --format='value(status.url)'

cat <<'NOTE'

Next steps:
1) Set non-empty secret values:
   echo -n "yourvalue" | gcloud secrets versions add BINANCE_READONLY_KEY --data-file=-
   # ... repeat for the other secrets

2) Smoke test:
   curl -s $(gcloud run services describe '"${SERVICE_NAME}"' --region '"${REGION}"' --format='value(status.url)')/status

Rollback:
   gcloud run revisions list --service "${SERVICE_NAME}" --region "${REGION}"
   # then update traffic to previous revision.
NOTE
