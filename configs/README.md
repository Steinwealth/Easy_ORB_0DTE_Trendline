# Easy ORB Strategy — application configuration

This document is the **canonical reference** for configuration under `configs/` for the integrated app: **Easy ORB (ETF / SO)**, **ORB 0DTE**, and **Trendline 0DTE**. It describes what each file owns, how keys are merged at startup, and where to look next (code + deeper docs).

---

## Contents

1. [Files in this folder](#files-in-this-folder)
2. [How configuration is loaded](#how-configuration-is-loaded)
3. [Shared layer (cross-path)](#shared-layer-cross-path)
4. [Path files and unified section groups](#path-files-and-unified-section-groups)
5. [Execution paths at a glance](#execution-paths-at-a-glance)
6. [Merge order and precedence](#merge-order-and-precedence)
7. [Runtime flags from main.py](#runtime-flags-from-mainpy)
8. [Reading configuration in code](#reading-configuration-in-code)
9. [Secrets: E*TRADE and Telegram](#secrets-etrade-and-telegram)
10. [Strategy modes](#strategy-modes)
11. [Production vs local](#production-vs-local)
12. [Duplicates, logging, and drift](#duplicates-logging-and-drift)
13. [Single-source rules](#single-source-rules)
14. [Further reading](#further-reading)
15. [New install checklist](#new-install-checklist)
16. [Quick verify](#quick-verify)
17. [History: removed files](#history-removed-files)

---

## Files in this folder

| File | Role |
|------|------|
| **`Data.env`** | Broker, data stack, watchlists/paths, GCP / deploy-oriented defaults |
| **`Shared.env`** | Cross-path orchestration (ORB tolerance, combined options book cap, hygiene flags, …) |
| **`ORBSO.env`** | **ORB Standard Order (ETF)** path — SO/ORR schedule, ORB window, SO 7:30 gating, capital split |
| **`ORB0DTE.env`** | **ORB 0DTE** path — Convex, priority, hard gate, chain health, path-scoped `OPTION_STEALTH_*` for ORB/spreads |
| **`Trendline0DTE.env`** | **Trendline 0DTE** path — signal, caps, option selection, `OPTION_STEALTH_TLINE_*`, build budgets, monitors |
| **`Risk.env`** | Position sizing, portfolio risk, slip guard, equity stealth |
| **`Alerts.env`** | Notifications, Telegram routing, alert throttles |
| **`CONFIG_AUDIT_ORB_0DTE_TRENDLINE.md`** | Static key audit (ORB / 0DTE / Trendline vs code references) — not loaded at runtime |
| **`README.md`** | This guide |

Former monolithic bundles (`base.env`, `automation.env`, `deployment.env`, `trading-parameters.env`, `modes/*.env`, `environments/*.env`) are **folded** into the seven `.env` files and `modules/strategy_mode_presets.py` (see [History](#history-removed-files)).

---

## How configuration is loaded

1. **`main.py`** calls `load_app_config()` → `modules.config_loader.load_configuration(strategy_mode, automation_mode, environment)`.
2. **`ConfigLoader`** reads the seven `configs/*.env` files in a **fixed order** (see [Merge order](#merge-order-and-precedence)), then applies **`modules/strategy_mode_presets`** when `strategy_mode` is `advanced` or `quantum`, then merges **`secretsprivate/*.env`** when not production.
3. After merge, **`load_app_config()`** copies every key from the merged dict into **`os.environ`** (string values) so modules that call `os.getenv` still see file-based defaults.

**Precedence (highest wins last):** Cloud Run / shell **`os.environ`** exports → merged file chain (later file / later line wins) → code defaults where keys are absent.

Loader implementation: `modules/config_loader.py` (`ConfigLoader.load_configuration`).

---

## Shared layer (cross-path)

Edit these when the change applies to **more than one** path or to **infrastructure** (broker, data, risk book, alerts):

| File | Use for |
|------|---------|
| **`Data.env`** | `BROKER_TYPE`, E*TRADE / Polygon / Yahoo knobs, watchlist paths, GCP / container-style keys |
| **`Shared.env`** | `PRIMARY_SIGNAL_GENERATOR`, `REQUIRE_LIVE_OPTION_DATA`, `MAX_TOTAL_OPTION_POSITIONS`, `ORB_BREAK_TOLERANCE`, `ORB_RTH_INTRADAY_SYMBOLS`, red-day / duplicate-trade / execution snapshot flags |
| **`Risk.env`** | Portfolio ceilings, per-trade risk, slip guard, stealth equity exits, `MAX_OPEN_POSITIONS` (risk-manager ceiling across paths) |
| **`Alerts.env`** | Telegram / webhooks / email toggles, alert routing and throttles |

Automation / deployment **slices** from legacy envs live in **`Data.env`**, **`Shared.env`**, and **`Alerts.env`** (not in separate `automation.env` / `deployment.env` files anymore).

---

## Path files and unified section groups

`ORBSO.env`, `ORB0DTE.env`, and `Trendline0DTE.env` each use the **same seven numbered section titles** (same order) inside the file so you can compare paths side by side. If a group is **not used** on a path, the file contains a short **n/a** comment for that group.

| # | Section | **ORBSO** (ETF SO) | **ORB0DTE** | **Trendline0DTE** |
|---|---------|-------------------|-------------|---------------------|
| 1 | Path toggle & scope | `ENABLE_ORB_STRATEGY` | `ENABLE_0DTE_STRATEGY`, demo chain | `ENABLE_TRENDLINE_STRATEGY`, universe / target |
| 2 | ORB & session timing | `ORB_WINDOW_*`, `SO_ENTRY_TIME`, `SO_CUTOFF_TIME` | `0DTE_ORB_TREAT_NEUTRAL_*` | Hold / confirm / session cutoffs (e.g. `TRENDLINE_NO_NEW_ENTRIES_AFTER_PT`) |
| 3 | Eligibility, scoring & filters | `SO_RECHECK_*`, `SO_ADAPTIVE_*` | Convex + chop bypass | Signal, regime, re-arm, impulse, structure gates |
| 4 | Position sizing & capital | `MAX_CONCURRENT_TRADES`, `SO_*` / `ORR_*` / cash % | Path caps, capital %, rank multipliers | Daily / slot / open caps, allocation %, demo ledger |
| 5 | Instruments, spreads & liquidity | *(n/a — ETF batch)* | Spreads, lotto sleeve, OI/volume, delta / momentum ladders | `TRENDLINE_OPTION_*` |
| 6 | Execution gating & runtime checks | *(SO batch: see §3–4)* | Recheck, strictness, hard gate, chain latency / monitor | Min expected move, 7:30 build batch budgets |
| 7 | Exits, stealth, routing & monitoring | *(ETF: Risk / Alerts / Shared)* | Partials, symbol map, priority, path `OPTION_STEALTH_*` | Exit ladder, `OPTION_STEALTH_TLINE_*`, monitor / cap logs |

---

## Execution paths at a glance

| Path | Role | Typical key prefixes | First place to edit |
|------|------|----------------------|---------------------|
| **ORB ETF (SO)** | Scheduled SO / ORR / ETF-style ORB | `ENABLE_ORB_STRATEGY`, `SO_*`, `ORR_*`, `ORB_WINDOW_*`, `MAX_CONCURRENT_TRADES` | **`ORBSO.env`**, then **`Risk.env`** for book-wide risk |
| **ORB 0DTE** | 7:30-window 0DTE options (ORB-driven) | `ENABLE_0DTE_STRATEGY`, `0DTE_*`, `ORB_0DTE_*`, `ORB_OPTIONS_MONITOR_*` | **`ORB0DTE.env`**, then **`Shared.env`** / **`Risk.env`** for cross-path caps and discipline |
| **Trendline 0DTE** | 0DTE on trendline breaks | `ENABLE_TRENDLINE_STRATEGY`, `TRENDLINE_*`, `OPTION_STEALTH_TLINE_*` | **`Trendline0DTE.env`** — see **`docs/0DTETrendline.md`** (Key Config Knobs) |

---

## Merge order and precedence

**Canonical load order** (each step overwrites duplicate keys from earlier steps):

1. **`Data.env`**
2. **`Shared.env`**
3. **`ORBSO.env`**
4. **`ORB0DTE.env`**
5. **`Trendline0DTE.env`**
6. **`Risk.env`**
7. **`Alerts.env`**
8. **`modules/strategy_mode_presets.py`** — only if `strategy_mode` is **`advanced`** or **`quantum`** (overrides overlapping keys from steps 1–7)
9. **`secretsprivate/*.env`** — when `ENVIRONMENT` is **not** `production` (e.g. `etrade.env`, `telegram.env`)

Within a **single** file, **later lines win** on duplicate keys (some files append former `modes/standard.env` + `environments/development.env` defaults at the end).

---

## Runtime flags from main.py

After all files load, `ConfigLoader` **always** sets (from CLI / env defaults in `load_app_config()`):

| Key | Source | Notes |
|-----|--------|--------|
| `STRATEGY_MODE` | `--strategy-mode` or `STRATEGY_MODE` env | `standard` \| `advanced` \| `quantum` |
| `AUTOMATION_MODE` | Derived from `--etrade-mode` | `demo` or `live` (not read from a standalone `automation.env`) |
| `ENVIRONMENT` | `--environment` or `ENVIRONMENT` env | `development` \| `production` \| `sandbox` |

Values for these three keys inside the seven `.env` files are **not authoritative**; they are overwritten every startup. Use **Cloud Run / shell** env for production-only tuning (`LOG_LEVEL`, `ETRADE_SANDBOX`, pool sizes, etc.).

---

## Reading configuration in code

| Mechanism | Behavior |
|-----------|----------|
| **`get_config_value(key, default)`** | Checks **`os.environ` first**, then merged config from `ConfigLoader` |
| **`os.getenv`** | Sees the same values after `load_app_config()` exports the merged dict |
| **`CONFIG_MISSING_KEY`** | Logged once per missing config-like key when code falls back (see `config_loader`) |
| **`CONFIG_DUPLICATE_KEY`** | Info log when the same key is set from more than one merged source |

---

## Secrets: E*TRADE and Telegram

| Concern | Local / non-production | Production (`ENVIRONMENT=production`) |
|---------|-------------------------|----------------------------------------|
| **E*TRADE consumer key/secret, account ids** | `secretsprivate/etrade.env` (from `etrade.env.template`) | Secret Manager + Cloud Run env — **`secretsprivate/` is not merged** |
| **OAuth access tokens** | Same file or env; webhook `main.py` **`/api/oauth/token-renewed`** | Secret **`etrade-oauth-prod`** (see `modules/prime_etrade_trading.py`, `ETradeOAuth/`) |
| **Telegram** | `secretsprivate/telegram.env` if present | Secret Manager or env — do **not** commit real tokens in **`Alerts.env`** |

Do **not** recreate `configs/etrade-oauth.env`; it was never merged and caused drift with `secretsprivate/etrade.env`.

---

## Strategy modes

| Mode | Config source |
|------|----------------|
| **`standard`** | The seven `configs/*.env` files only (includes former `standard.env` + `development.env` defaults merged into the files, May 2026) |
| **`advanced`**, **`quantum`** | Same seven files, then **`modules/strategy_mode_presets.py`** overlays (replaces former `configs/modes/advanced.env` / `quantum.env`) |

Edit presets in **`modules/strategy_mode_presets.py`** when you want different aggressiveness / workers / caps for non-standard modes.

---

## Production vs local

- **`ENVIRONMENT=production`:** `ConfigLoader` **skips** `secretsprivate/` — rely on **Secret Manager** and **Cloud Run env** for secrets and overrides.
- **Repo defaults** align with former **development** tuning (merged into the seven files). There is **no** `configs/environments/production.env` anymore; production differences should be **explicit shell / Cloud** env vars.

---

## Duplicates, logging, and drift

- Many keys intentionally appear in **multiple** files. Use startup **`CONFIG_DUPLICATE_KEY`** lines to see which file won.
- Prefer **one canonical file per concern** ([Single-source rules](#single-source-rules)); delete stale duplicates when you touch an area.
- **`get_config_value`** prefers **`os.environ`** — deploy-time exports override file defaults by design.

---

## Single-source rules

| Topic | Canonical file |
|--------|------------------|
| Enable ORB / 0DTE / Trendline | **`ORBSO.env`**, **`ORB0DTE.env`**, **`Trendline0DTE.env`** (one path toggle per file) |
| ORB window, SO entry/cutoff | **`ORBSO.env`** |
| SO / ORR / cash % | **`ORBSO.env`** |
| `MAX_CONCURRENT_TRADES` (SO ETF 7:30 batch) | **`ORBSO.env`** |
| `0DTE_MAX_POSITIONS` and 0DTE execution guardrails | **`ORB0DTE.env`** |
| `MAX_TOTAL_OPTION_POSITIONS` (combined options book) | **`Shared.env`** |
| Trendline caps, build budgets, signal keys, `OPTION_STEALTH_TLINE_*` | **`Trendline0DTE.env`** |
| Generic `OPTION_STEALTH_*` / live-quote policy | **`Shared.env`** or **`Risk.env`** |
| Portfolio ceiling `MAX_OPEN_POSITIONS` | **`Risk.env`** (≥ sum of path caps so path gates hit first) |
| Mode-specific aggressiveness | **`modules/strategy_mode_presets.py`** (`advanced` / `quantum` only) |
| Easy 0DTE **standalone** reference (not merged by default) | `easy0DTE/configs/0dte.env` |

---

## Further reading

| Document | Contents |
|----------|----------|
| **`CONFIG_AUDIT_ORB_0DTE_TRENDLINE.md`** | Static audit: which merged keys match code references for the three paths |
| **`docs/0DTETrendline.md`** | Trendline module map, 7:30 build, **Key Config Knobs** for `TrendlineConfig` |
| **`docs/0DTEORB.md`** | Integrated ORB 0DTE surfaces, caps, live-option policy |
| **`docs/ORB0DTE_Config_Reference.md`** | ORB 0DTE path stages vs modules |
| **`docs/Risk.md`** | Risk manager, `MAX_OPEN_POSITIONS`, path ceilings |
| **`docs/Settings.md`** | Broader workspace settings (some paths still being updated to the seven-file model) |
| **`docs/Alerts.md`** | Telegram setup and alert behavior |
| **`docs/CloudSecrets.md`** | Cloud / Secret Manager alignment |

---

## ORB SO ranking refinement knobs

`ORBSO.env` includes the primary soft-ranking controls for 7:30 ETF SO prioritization:

- `SO_CONTINUATION_MOMENTUM_WEIGHT` (boost healthy continuation quality)
- `SO_EXHAUSTION_PENALTY_WEIGHT` (penalize wide + extended + decelerating setups)
- `SO_MAX_EXTENSION_SOFT_PENALTY` (cap on distance/extension chasing penalty)
- `SO_ORB_RANGE_SOFT_PENALTY` (soft drag on very wide ORB ranges)
- `SO_MOMENTUM_DECELERATION_PENALTY` (penalize weakening momentum while extending)

`SO_WINNER_*` keys are also defined in `ORBSO.env` so winner-profile filtering resolves from merged config instead of silent runtime defaults.

---

## New install checklist

1. Clone the repo and `cd` into **`1. The Easy ORB Strategy`**.
2. Copy **`secretsprivate/etrade.env.template`** → **`secretsprivate/etrade.env`** and fill consumer keys / account ids (see `secretsprivate/README.md`).
3. Optionally copy **`secretsprivate/telegram.env.template`** → **`telegram.env`** for local Telegram.
4. Edit the **seven** `configs/*.env` files for your strategy: enable paths in **`ORBSO.env`** / **`ORB0DTE.env`** / **`Trendline0DTE.env`**, tune **`Risk.env`**, confirm **`Data.env`** broker + lists.
5. Run [**Quick verify**](#quick-verify) with Python **3.10+** (project code uses modern `dataclass` / typing; **3.11** is a safe default).

---

## Quick verify

From the strategy repo root (adjust the `cd` path to your machine):

```bash
cd "/path/to/0. Strategies and Automations/1. The Easy ORB Strategy" && python3.11 -c "import sys; sys.path.insert(0,'.'); from modules.config_loader import load_configuration; c=load_configuration('standard','demo','development'); print('OK', c.get('ENABLE_ORB_STRATEGY'), c.get('BROKER_TYPE'), c.get('MAX_OPEN_POSITIONS'))"
```

Expect **`MAX_OPEN_POSITIONS`** = **26** by default in **`Risk.env`** (portfolio ceiling; path-specific caps are lower in each path file).

---

## History: removed files

| Removed | Replacement |
|---------|-------------|
| `base.env`, `automation.env`, `deployment.env`, `trading-parameters.env` | Distributed into the **seven** `configs/*.env` files (May 2026) |
| `configs/modes/*.env`, `configs/environments/*.env` | Merged into the seven files + **`strategy_mode_presets`** + in-file dev defaults |
| `configs/*.env.template` (alerts, automation, base, deployment) | Deleted after full **key-name parity** with the seven files; use **`secretsprivate/etrade.env.template`** for greenfield secrets |
| `performance*.env`, `symbol-scoring.env`, `cloudflare-aws-config.env`, `optimized_env_template.env`, redundant `configs/etrade-oauth*` | Removed earlier — do not resurrect |

---

*Last updated: May 2026 — seven-file layout, unified path section groups, `Alerts.env` naming, presets for `advanced` / `quantum`.*
