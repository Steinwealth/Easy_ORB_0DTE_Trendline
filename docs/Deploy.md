# Deploying the Easy ORB / 0DTE Trading Service

**Project:** `easy-etrade-strategy`  
**Service:** `easy-etrade-strategy` (Cloud Run — **ORB ETF + ORB 0DTE + Trendline 0DTE** shared runtime)  
**Path (project root):** `0. Strategies and Automations/1. The Easy ORB Strategy`

This is a **quick operator checklist** for deploying code changes for the trading service to Cloud Run. For full cloud details see `Cloud.md` and `CloudSecrets.md`.

**Keep uploads lean:** Read **[§2 Lean Cloud Build source](#2-lean-cloud-build-source-upload-mandatory-before-each-deploy)** before every `gcloud builds submit` so future deploys stay fast and under the size budget.

---

## 0. Deployment policy (strict)

- **No auto-deploy:** code/doc updates do not imply deployment.
- Deploy only on an explicit instruction: **"deploy now"**.
- During active market session, do not deploy unless emergency rollout is explicitly approved.
- Deploy scripts are gated and require explicit confirmation variable:
  - `DEPLOY_NOW=YES`

---

## 1. Pre-deploy checklist

- **Local path:** You are in the ORB Strategy root:
  - `cd "0. Strategies and Automations/1. The Easy ORB Strategy"`
- **gcloud configured to correct project:**
  - `gcloud config set project easy-etrade-strategy`
- **Auth:** You are logged in and allowed to deploy:
  - Permission to act as `etrade-strategy-sa@easy-etrade-strategy.iam.gserviceaccount.com`
  - See `CloudSecrets.md` → *Service Names and URLs* if you need to re-check roles.
- **Code state:**
  - All desired fixes are committed (optional but recommended).
  - `BUILD_ID.txt` updated if you want a new build marker layer.

---

## 2. Lean Cloud Build source upload (mandatory before each deploy)

Cloud Build uploads a **source tarball** from the ORB Strategy directory before building the image. Size and contents are controlled by **ignore files**, not by “whatever is on disk.”

### 2.1 Two different ignore files

| File | Purpose |
|------|---------|
| **`.gcloudignore`** | Lists paths **not** sent to Google Cloud Build with `gcloud builds submit`. **This determines upload size and speed.** |
| **`.dockerignore`** | Lists paths **hidden from `docker build`** when Cloud Build runs the `Dockerfile`. Usually aligned with `.gcloudignore` for anything that should never enter an image. |

If you add new runtime code or data, ensure **neither** file accidentally excludes it (watch broad patterns like `*.json` — exceptions exist for holidays and watchlist).

### 2.2 Size budget and verification

**Target:** keep the **uncompressed** uploaded source set **under ~4 MiB** (operational guardrail; verify after changing ignores or adding large assets).

From the ORB Strategy root, **before** `gcloud builds submit`:

```bash
cd "0. Strategies and Automations/1. The Easy ORB Strategy"

gcloud meta list-files-for-upload . | wc -l
gcloud meta list-files-for-upload . | xargs stat -f%z 2>/dev/null | awk '{s+=$1} END {printf "bytes=%d (%.3f MiB)\n", s, s/1024/1024}'
```

With current ignores you should see on the order of **90–100 files** and **under ~4 MiB**. If the byte total jumps, inspect what became eligible (new folders under the repo root, accidental removal of an ignore rule, or local junk — see below).

### 2.3 Never upload / never commit

- **`.gcloud_tmp/`** — If this directory exists **inside** the repo folder, it is **local gcloud state** (logs, SQLite DBs, credential-related caches). It must **not** be uploaded or committed. It is listed in `.gitignore`; delete locally if present: `rm -rf .gcloud_tmp` (gcloud will recreate config under your user profile as needed).

### 2.4 What stays out of the tarball on purpose

Examples (see `.gcloudignore` for the authoritative list):

- **`docs/`**, `**/*.md`**, session notes, **`easyCollector/`** (separate service), tests, deploy helper scripts (except the two cleanup shells copied into the image).
- **Legacy / unused Python:** `modules/symbol_score_integration.py` (no production imports — verified periodically; keeps the bundle lean).
- **Duplicate dependency list:** `easy0DTE/requirements.txt` — the image installs **`requirements.txt`** at the repo root only.
- **Optional watchlist JSON:** `data/watchlist/sentiment_pairs_mapping.json` — not referenced by Python; **`complete_sentiment_mapping.json`** and **`orb_inverse_mapping.json`** are used. Re-include this file in uploads only after adding runtime code that reads it (and trim elsewhere if you need to stay under the size budget).

### 2.5 What must upload for all three paths (ORB, ORB 0DTE, Trendline 0DTE)

Everything the **`Dockerfile`** copies or that those trees import at runtime, including:

- `Dockerfile`, `.dockerignore`, `main.py`, `cloud_run_entry.py`, root **`requirements.txt`**, `BUILD_ID.txt`
- **`modules/`** (production Python for ORB core, stealth, alerts, risk, etc.)
- **`configs/*.env`** — merged app configuration (`Data`, `Shared`, `ORBSO`, `ORB0DTE`, `Trendline0DTE`, `Risk`, `Alerts`)
- **`data/watchlist/`** — `core_list.csv`, `0dte_list.csv`, mapping JSON the code loads, **`data/holidays_*.json`**
- **`easy0DTE/`** — package + `modules/` + `configs/0dte.env` (no standalone `requirements.txt` in image)
- **`easyTrendline/`** — full Python package for Trendline 0DTE
- **`scripts/cleanup_old_images.sh`**, **`scripts/cleanup_old_revisions.sh`** — copied into the image for `/api/cleanup/images`

When you add a **new** top-level package or data directory: update the **`Dockerfile`** `COPY` lines if it is not already under an existing copy, and add **no** broad ignore rules that would drop it.

---

## 3. Build the container image

From the ORB Strategy root:

```bash
cd "0. Strategies and Automations/1. The Easy ORB Strategy"
gcloud config set project easy-etrade-strategy

gcloud builds submit \
  --tag gcr.io/easy-etrade-strategy/easy-etrade-strategy:latest .
```

Notes:
- This uploads the current directory **subject to `.gcloudignore`** as build context and runs the `Dockerfile`.
- The resulting image is pushed as:
  - `gcr.io/easy-etrade-strategy/easy-etrade-strategy:latest`
- **Image contents (three trading paths):** `modules/` + `main.py` (ORB ETF/core), **`easy0DTE/`** (ORB 0DTE options), **`easyTrendline/`** (Trendline 0DTE). The Dockerfile **RUN** step fails the build if checked entrypoints for those paths are missing.

---

## 4. Deploy to Cloud Run

After a successful build, deploy the new image to the trading service:

```bash
gcloud run deploy easy-etrade-strategy \
  --image gcr.io/easy-etrade-strategy/easy-etrade-strategy:latest \
  --platform managed --region us-central1 \
  --memory 2Gi --cpu 2 --max-instances 1 --min-instances 0 \
  --concurrency 80 --timeout 3600 --no-cpu-throttling \
  --service-account etrade-strategy-sa@easy-etrade-strategy.iam.gserviceaccount.com \
  --set-env-vars="ENVIRONMENT=production,STRATEGY_MODE=standard,ETRADE_MODE=demo,SYSTEM_MODE=full_trading,CLOUD_MODE=true,ENABLE_ORB_STRATEGY=true,ENABLE_0DTE_STRATEGY=true,ENABLE_TRENDLINE_STRATEGY=true,LOG_LEVEL=INFO" \
  --allow-unauthenticated
```

Key points:
- **Project:** `easy-etrade-strategy`
- **Region:** `us-central1`
- **Env vars:** match the production trading configuration (demo E*TRADE, full trading system, 0DTE enabled).

---

## 5. Post-deploy health checks

After `gcloud run deploy` reports success:

1. **Check service URL** (see `CloudSecrets.md`):
   - `https://easy-etrade-strategy-223967598315.us-central1.run.app`
2. **Health endpoint (optional):**
   - `GET /health` on the service URL should return OK.
3. **Logs:**
   - Use Cloud Logging to confirm the new revision is running and pipeline steps are firing:
     - `PIPELINE | STEP 1 ORB CAPTURE`
     - `PIPELINE | STEP 4 SIGNAL COLLECTION`
     - `PIPELINE | STEP 5 TRADE EXECUTION`
4. **Scheduler jobs:** (only if changed or after a long pause)
   - Validation/Prefetch jobs and trading jobs should still point to the correct service URL.
   - Reference `CloudSecrets.md` → *Cloud Scheduler Jobs (Project-Specific)* for commands.

---

## 6. Safe deploy habits

- Prefer **end-of-session deploys** to avoid changing behavior mid-trading-day.
- If a deploy contains non-trivial trading logic changes:
  - Verify Signal Collection and alerts on the next open before enabling live accounts.
  - Keep Cloud Logging open during 6:30–7:45 AM PT to confirm ORB/0DTE flows.
- Use cleanup scripts periodically to control image/revision sprawl:
  - See `CloudSecrets.md` → *Cloud Cleanup* for `cleanup_old_images.sh` and `cleanup_old_revisions.sh`.

---

*Short version: verify **[§2](#2-lean-cloud-build-source-upload-mandatory-before-each-deploy)** upload size and file list, then from the ORB Strategy root run `gcloud builds submit`, then `gcloud run deploy`, and verify the trading service URL and logs in GCP.* 

