## Atlas News Intelligence — Monorepo

A multi-service GCP project that ingests fresh news from GDELT, extracts entities with AI, stores enriched records in Firestore, and visualizes global and per-country trends in a dashboard.

Current deployment: https://news-atlas-1010480476071.europe-central2.run.app/

It implements 4 GCP services:

### 1) AI API

**Purpose:** A thin API layer that exposes AI functionality (Vertex AI, OpenAI) to other internal services and tools.

**Key responsibilities**

* Text and entity utilities (embeddings, classification, extraction helpers).
* Abstraction over providers (Vertex AI, OpenAI).
* Centralized configuration and rate limiting.

**Interfaces**

* REST endpoints (FastAPI/uvicorn).
* Auth via GCP IAM / API keys (depending on deployment).

**Dependencies**

* Vertex AI (via ADC).
* OpenAI API (via `OPENAI_API_KEY`).

---

### 2) News Publisher

**Purpose:** Periodically queries GDELT for the recent timeframe and publishes each article as a discrete message.

**Key responsibilities**

* Fetch recent articles from GDELT.
* Normalize/clean article payload (title, URL, language, country codes, timestamps).
* Publish one message per article to **Pub/Sub topic `news.raw`**.

**Interfaces**

* Internal HTTP endpoint (health, manual trigger if needed).
* Pub/Sub **topic** output.

**Scheduling**

* Typically triggered on a schedule (see [Scheduling the publisher](#scheduling-the-publisher)).

---

### 3) News Processor

**Purpose:** Subscribes to the publisher’s topic, performs entity extraction, and persists results to Firestore. Maintains aggregated counters.

**Key responsibilities**

* Consume messages from **subscription** attached to `news.raw`.
* Call AI API to extract entities (people, orgs, locations, topics).
* Save enriched article documents to **Firestore** collection `articles`.
* Update aggregations:

  * Per-country counts → collection `/mentions/{hour}/countries` 
  * Per-entity counts → collection  `/mentions/{hour}/entities` 
* Increments are done on **create** (idempotent logic to avoid double counting on retries).

**Interfaces**

* Pub/Sub **subscription** input.
* Firestore read/write.

---

### 4) Atlas Dashboard

**Purpose:** Dash app providing a global overview and drill-downs.

**Features**

* Number of articles per country (map or table).
* Global hot topics and per-country hot topics.
* Direct links to source articles.
* Basic filters (time window, country, topic).


### Event flow

1. **Publisher** queries GDELT for “last N minutes”.
2. For each article, **Publisher** publishes a message to Pub/Sub `news.raw`.
3. **Processor** receives messages, calls **AI API** for entity extraction.
4. **Processor** writes a document to Firestore `articles` and increments counters in `agg_country` and `agg_entity`.
5. **Dashboard** reads from Firestore and renders live metrics & links.

---

## Local Development

### Prerequisites

* Python 3.10+ and `pip`
* Docker
* `gcloud` CLI
* Access to a GCP project with appropriate permissions to create service account and grant roles

### Authenticate to GCP

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

### Service Account impersonation for local runs

If you don’t have broad roles, **impersonate** the service accounts used by each service. The simplest way to make client libraries pick this up locally is:

```bash
# Use a user that is allowed to impersonate the SA
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Application Default Credentials using SA impersonation:
gcloud auth application-default login --impersonate-service-account=SERVICE_ACCOUNT_NAME@YOUR_PROJECT_ID.iam.gserviceaccount.com
```

That writes ADC to your local gcloud config such that Google client libraries will obtain short-lived tokens **as the service account**. Repeat with the appropriate SA per service.

> You must have the IAM role **Service Account Token Creator** (or equivalent) on the target SA to impersonate it.

### Run services locally

> From the repo root unless otherwise noted.

**News Publisher (FastAPI)**

```bash
cd news_publisher
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8080
```

**Atlas Dashboard (Dash)**

```bash
cd atlas
pip install -r requirements.txt
python app.py
```

**Notes**

* Make sure your environment variables are set (see [Common environment variables](#common-environment-variables)).
* ADC must be available (either direct `application-default login` or SA impersonation as above).

---

## Deployment

Each service is packaged as a Docker image. Every service folder contains a `build.sh` that performs idempotent setup and deployment.

### What the `build.sh` scripts do

Per service, the scripts will typically:

1. **Create or ensure** existence of:

   * A dedicated **service account**.
   * Required **IAM bindings** (least privilege).
   * **Artifact Registry** repository (if needed).
   * **Pub/Sub** topic/subscription (publisher/processor).
   * **Firestore** database (must be enabled once per project; location must be set manually the first time).

2. **Build** the Docker image.

3. **Push** the image to Artifact Registry.

4. **Deploy** the image to your chosen runtime (e.g., Cloud Run, GKE, or Compute).

   > The scripts are idempotent: re-running them won’t recreate resources unnecessarily.

### Per-service deploy

From the repo root:

```bash
cd ai_api
./build.sh
```

```bash
cd news_publisher
./build.sh
```

```bash
cd news_processor
./build.sh
```

```bash
cd atlas
./build.sh
```

> Consult each folder’s `build.sh` for service-specific flags (e.g., region, min instances).
