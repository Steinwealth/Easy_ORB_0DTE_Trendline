# Data Sources Troubleshooting: Polygon (US) and Coinbase (Crypto)

When **US Market** or **Crypto Market** snapshots are not succeeding, the cause is usually one of:

1. **Polygon (US)**: `POLYGON_API_KEY` not available to Cloud Run (secret missing, no version, or IAM).
2. **Crypto**: `volume_delta` validation (fixed in code; redeploy) or Coinbase unreachable/empty.

---

## One-command diagnostic and fix

You do **not** need to `cd` into `easyCollector`. Run from the **ORB Strategy root** (the folder that contains `easyCollector` and `deploy-collector.sh`):

```bash
./easyCollector/scripts/ensure_data_sources_ready.sh
```

With automatic IAM fix (grants the Cloud Run service account `secretAccessor` on `polygon-api-key` if missing):

```bash
./easyCollector/scripts/ensure_data_sources_ready.sh --fix-iam
```

With an explicit service URL (if `gcloud` is not configured or you want to test a specific URL):

```bash
./easyCollector/scripts/ensure_data_sources_ready.sh https://easy-collector-XXXXX-uc.a.run.app
./easyCollector/scripts/ensure_data_sources_ready.sh --fix-iam https://easy-collector-XXXXX-uc.a.run.app
```

**If you see `cd: no such file or directory: easyCollector`** — you ran `cd easyCollector` from a folder that doesn’t contain it. From the ORB Strategy root, run `./easyCollector/scripts/ensure_data_sources_ready.sh` (no `cd` needed). If you prefer to run from inside `easyCollector`: `cd easyCollector` first (from the ORB root), then `./scripts/ensure_data_sources_ready.sh`.

The script:

- Checks that `polygon-api-key` exists in Secret Manager and has a version.
- Checks that the Cloud Run service account has `roles/secretmanager.secretAccessor` on `polygon-api-key` (and can add it with `--fix-iam`).
- Fetches the Cloud Run URL (or uses the one you pass), then calls:
  - `GET /health` (allow up to ~45s on cold start)
  - `GET /debug/polygon` (key set? Polygon health?)
  - `GET /debug/us/provider_smoke?symbol=SPY&bars=50` (US data)
  - `GET /debug/crypto/product_smoke?symbol=BTC-PERP` (Crypto data)
- Prints a short summary and what to do next.

If it seems to stop or show nothing after `GET /health`, the service may be cold‑starting. Test manually:  
`curl -s -m 60 'https://easy-collector-XXXXX-uc.a.run.app/health'`

---

## Manual checklist

### 1. Polygon API key in Secret Manager

**Create or update the secret** (value from `secretsprivate/.env`):

```bash
./secretsprivate/ensure_secret_manager_polygon.sh
```

Requires `secretsprivate/.env` with `POLYGON_API_KEY`. If the secret exists but has no versions:

```bash
# From project root
echo -n "YOUR_POLYGON_API_KEY" | gcloud secrets versions add polygon-api-key \
  --project=easy-etrade-strategy --data-file=-
```

**Validate the key against Polygon** (optional):

```bash
./scripts/check_polygon_secret_ready.sh --validate-key
```

To test a **new key before** adding to Secret Manager:  
`./scripts/validate_polygon_key.sh --key=YOUR_KEY`  
If you get **403/401**: the key is invalid, expired, or your plan does not include US stocks aggregates. Check polygon.io/dashboard (or massive.com) and order a new key if discontinued.

### 2. IAM: Cloud Run can read the secret

The Cloud Run service account needs `roles/secretmanager.secretAccessor` on `polygon-api-key`:

```bash
PROJECT_ID=easy-etrade-strategy
# Get project number and default compute SA
PROJ_NUM=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
SA="${PROJ_NUM}-compute@developer.gserviceaccount.com"

gcloud secrets add-iam-policy-binding polygon-api-key \
  --project=$PROJECT_ID \
  --member="serviceAccount:${SA}" \
  --role="roles/secretmanager.secretAccessor"
```

Or use:

```bash
./scripts/ensure_data_sources_ready.sh --fix-iam
```

### 3. Redeploy after secret or IAM changes

Cloud Run only gets the secret at deploy time. After creating/updating the secret or IAM:

```bash
# From the parent of easyCollector (ORB Strategy root)
./deploy-collector.sh
```

### 4. Verify the running service

```bash
./scripts/check_polygon_coinbase.sh
# or
./scripts/ensure_data_sources_ready.sh
```

Expect:

- **US**: `provider=polygon`, `row_count` ≥ 10 (or similar from `provider_smoke`).
- **Crypto**: `resolved_product_id=BTC-USD`, `candle_row_count` > 0.

### 5. Crypto: `volume_delta` and Coinbase

- **`volume_delta`**  
  The service must run a build that coerces `volume_delta` to `int` before `VolumeVWAPData`. If you see:

  `Input should be a valid integer, got a number with a fractional part`

  redeploy with the latest `snapshot_service` that includes that fix.

- **Coinbase 429 / empty**  
  The client uses 100 ms between symbols and tenacity retries. If `product_smoke` returns 0 candles, check:

  - Network from Cloud Run to `api.exchange.coinbase.com`.
  - `GET /debug/crypto/product_smoke?symbol=BTC-PERP` and `.../ETH-PERP` for `error` or `reason`.

---

## Quick reference

| Check | Command |
|-------|---------|
| Secret + IAM + deploy wiring | `./scripts/check_polygon_secret_ready.sh` |
| Validate Polygon key vs API | `./scripts/check_polygon_secret_ready.sh --validate-key` |
| Full diagnostic + optional IAM fix | `./scripts/ensure_data_sources_ready.sh [--fix-iam] [BASE_URL]` |
| Live US + Crypto smoke | `./scripts/check_polygon_coinbase.sh [BASE_URL]` |
| Create/update secret from `.env` | `./secretsprivate/ensure_secret_manager_polygon.sh` |
| Redeploy (from ORB root) | `./deploy-collector.sh` |

---

## See also

- **Data.md** §6 — Known failure modes (Polygon 403, yfinance, `volume_delta`, Coinbase 429, 0 snapshots).
- **SECRETS.md** — Full Polygon secret and deploy chain.
- **README.md** — Deployment, `check_polygon_secret_ready`, `check_polygon_coinbase`.
