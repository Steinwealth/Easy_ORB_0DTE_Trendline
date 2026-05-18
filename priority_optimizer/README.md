# Priority Optimizer

**89-point data collection for ORB and 0DTE strategies** — collect signal, ranking, and trade data from daily runs to analyze and optimize the priority formula, Red Day detection, and exit strategies.

**Last updated**: February 2026  
**Status**: ✅ Production active (integrated with Easy ORB 0DTE Strategy)

---

## Quick start

**Collect 89-point data for a trading day (recommended):**

```bash
cd priority_optimizer
python3 collect_89points_fast.py --date YYYY-MM-DD
```

**Examples:**

```bash
# Today's data (default)
python3 collect_89points_fast.py

# Specific date
python3 collect_89points_fast.py --date 2026-02-19
```

**What you get:**

- **89 data points** per signal (technical indicators, ORB data, ranking, trade P&L)
- **Output**: `comprehensive_data/YYYY-MM-DD_comprehensive_data.json` and `.csv` (local)  
- **GCS**: `gs://easy-etrade-strategy-data/priority_optimizer/comprehensive_data/` (auto-upload)
- **Time**: ~10–30 seconds for typical signal count; no E*TRADE login required; works anytime

**Supports:** ORB Strategy (SO) and 0DTE Options Strategy.

---

## Daily execution snapshot archiving (for dataset buildout)

Use this after each trading session to pull execution snapshots into a **date folder** and generate a companion summary file:

```bash
python3.11 retrieve_execution_snapshots.py --date YYYY-MM-DD
```

Output structure:

```text
priority_optimizer/retrieved_data/execution_snapshots/
└── YYYY-MM-DD/
    ├── YYYY-MM-DD_HHMMSS_execution_window_input.json
    ├── YYYY-MM-DD_HHMMSS_blocked_portfolio_red_day.json
    ├── ... (all snapshots for that session date)
    └── SESSION_SUMMARY.md
```

`SESSION_SUMMARY.md` is auto-generated as a working template for:
- day profitability (daily/weekly/account close values),
- profitable/unprofitable trade notes,
- Red Day decision review,
- optimization notes for execution, ranking, and risk thresholds.

If you already wrote notes and want to preserve them, rerun without overwrite.  
If you want to regenerate the template:

```bash
python3.11 retrieve_execution_snapshots.py --date YYYY-MM-DD --overwrite-summary
```

### Session observations (multi-day research)

After pairing EOD outcomes with snapshot features, add a **compact** note under **`session_observations/`** and one line in **`session_observations/INDEX.md`**:

- **`session_observations/README.md`** — how to grow the dataset and when formula changes are justified  
- **`session_observations/YYYY-MM-DD_orb_so.md`** — per-day interpretation (ORB ETF SO); mirror with `_orb_0dte` for options  
- **`retrieved_data/execution_snapshots/YYYY-MM-DD/SESSION_SUMMARY.md`** — full tables + EOD paste (source of truth for that day’s pairings)

---

## Purpose

The Priority Optimizer:

- Captures **full signal lists** (before filtering) and **execution results** (executed vs filtered)
- Records **trade performance** (entry, peak, exit, P&L) and **technical/ranking data**
- Stores **89 comprehensive data points** per signal for formula tuning, Red Day analysis, and exit optimization

Data is used to improve priority ranking, Red Day detection, position sizing, and exit triggers.

---

## Folder structure

```
priority_optimizer/
├── README.md                    # This file (function overview + quick start)
├── collect_89points_fast.py     # ⭐ Main script: fast 89-point collection (REST, no E*TRADE init)
├── collect_89points_rest.py     # REST-based collection (alternative)
├── collect_89points_complete.py # Full collection with E*TRADE fallback
├── collect_89points_etrade.py   # E*TRADE-based collection
├── collect_daily_89points.py    # Daily collection entrypoint
├── retrieve_gcs_89points.py     # Retrieve stored data from GCS
├── recover_gcs_data.py          # Recover historical data from GCS
├── reconstruct_89point_data.py # Reconstruct 89-point data from trade history
├── comprehensive_data/          # 89-point output (local): YYYY-MM-DD_comprehensive_data.json/.csv
├── retrieved_data/              # Retrieved GCS data (local)
├── recovered_data/             # Recovery output (local)
└── docs/
    └── 2026/
        ├── Jan7 Session/        # Jan 7 session: Red Day analysis, formula review, collection status
        └── Reference/           # Guides, datapoints reference, integration, recovery
```

---

## Scripts (function-critical)

| Script | Purpose |
|--------|---------|
| **collect_89points_fast.py** | **Recommended.** Fast 89-point collection via REST; no E*TRADE init; use `--date YYYY-MM-DD`. |
| collect_89points_rest.py | REST-based 89-point collection (alternative). |
| collect_89points_complete.py | Full collection with E*TRADE fallback (slower). |
| collect_89points_etrade.py | E*TRADE-based collection. |
| collect_daily_89points.py | Daily collection entrypoint. |
| retrieve_gcs_89points.py | Retrieve stored 89-point data from GCS. |
| retrieve_execution_snapshots.py | Retrieve execution-window feature snapshots from GCS to local. |
| recover_gcs_data.py | Recover historical data from GCS. |
| reconstruct_89point_data.py | Reconstruct 89-point records from trade history. |

---

## 89 data points (summary)

Per-signal data includes:

| Category | Count | Examples |
|----------|-------|----------|
| Price data | 5 | open, high, low, close, volume |
| Moving averages | 5 | sma_20/50/200, ema_12/26 |
| Momentum | 7 | rsi, macd, macd_histogram, momentum_10 |
| Volatility | 7 | atr, bollinger_*, volatility |
| Volume | 4 | volume_ratio, obv, ad_line |
| VWAP / RS | 3 | vwap, vwap_distance_pct, rs_vs_spy |
| ORB data | 6 | orb_high/low/open/close/volume, orb_range_pct |
| Trade data | 15 | entry/exit price/time, P&L, peak_price, exit_reason |
| Ranking | 6 | rank, priority_score, confidence, orb_volume_ratio |
| Risk | 8 | stop_loss, trailing, breakeven, max_adverse_excursion |
| Market conditions | 5 | market_regime, trend_direction, volume_regime |
| Other indicators | 16 | stoch, williams_r, adx, mfi, roc, etc. |
| **Total** | **89** | |

Full list and use-case notes: [docs/2026/Reference/DATAPOINTS_SUMMARY.md](docs/2026/Reference/DATAPOINTS_SUMMARY.md).

---

## GCS storage

- **Path**: `gs://easy-etrade-strategy-data/priority_optimizer/`
- **89-point data**: `comprehensive_data/YYYY-MM-DD_comprehensive_data.json`
- **Signals**: `daily_signals/`, `0dte_signals/` (from main app)
- **Retention**: Rolling window (e.g. last 50 days; see main strategy config)

Data is written automatically by the collection scripts and by the main trading app’s priority data collectors.

---

## Automatic collection (main app)

The Easy ORB 0DTE Strategy also collects priority/89-point data during the run:

- **ORB**: `modules/priority_data_collector.py` + `modules/comprehensive_data_collector.py`
- **0DTE**: `easy0DTE/modules/options_priority_data_collector.py`
- **Persistence**: `modules/daily_run_tracker.py` (GCS signal persistence)

Manual runs with `collect_89points_fast.py` are still recommended for ad-hoc or historical dates.

---

## Documentation (supporting docs)

All supporting docs live under **`docs/2026/`**. Only this README and the scripts stay in the root.

### docs/2026/Jan7 Session/

Session-specific analysis and recommendations (January 7, 2026):

- Red Day analysis and fix recommendations
- 89-point collection status and summary
- Priority formula review and updated formula notes

| Doc | Purpose |
|-----|---------|
| RED_DAY_ANALYSIS_JAN7.md | Red Day detection analysis (Jan 7) |
| RED_DAY_FIX_RECOMMENDATIONS.md | Red Day fix recommendations |
| DATA_COLLECTION_SUMMARY_JAN7.md | 89-point collection summary (Jan 7) |
| COLLECTION_STATUS_JAN7.md | Collection status (Jan 7) |
| CORRECT_UPDATED_PRIORITY_FORMULA.md | Correct updated priority formula (v2.2) |
| FORMULA_STATUS_CLARIFICATION.md | Formula status clarification |
| UPDATED_PRIORITY_FORMULA.md | Updated priority formula options |
| PRIORITY_FORMULA_REVIEW.md | Priority formula review |

### docs/2026/Reference/

Guides, datapoints reference, integration, and recovery:

| Doc | Purpose |
|-----|---------|
| HOW_TO_USE.md | How to use: one-command collection, what you get, verify |
| QUICK_START.md | Quick start (3 steps) |
| QUICK_COLLECTION_GUIDE.md | Detailed collection guide |
| DATA_COLLECTION_INDEX.md | Index of collected data and docs |
| DATAPOINTS_SUMMARY.md | 89 data points quick reference |
| DATAPOINTS_AND_HOLIDAYS_SUMMARY.md | Full 89 datapoints list + holiday list |
| 89_DATAPOINTS_ANALYSIS.md | Analysis: will 89 points suffice? |
| INTEGRATION_GUIDE.md | Trade execution integration |
| INTEGRATION_AUTOMATIC_COLLECTION.md | Automatic collection integration |
| README_RECOVERY.md | Data recovery from GCS |

---

## Related modules (main app)

| Module | Purpose |
|--------|---------|
| [modules/priority_data_collector.py](../modules/priority_data_collector.py) | ORB priority/signal data collection |
| [modules/comprehensive_data_collector.py](../modules/comprehensive_data_collector.py) | 89-point comprehensive collection |
| [modules/data_history_manager.py](../modules/data_history_manager.py) | Data history management |
| [easy0DTE/modules/options_priority_data_collector.py](../easy0DTE/modules/options_priority_data_collector.py) | 0DTE priority data collection |

---

## Analysis use cases

- **Priority ranking**: Tune weights (VWAP, RS vs SPY, ORB volume, confidence) using collected scores and outcomes.
- **Red Day detection**: Improve pattern logic using 89-point snapshots on red vs green days.
- **Exit optimization**: Use peak capture, trailing/breakeven, and exit reasons from trade data.
- **Position sizing**: Validate batch sizing and rank multipliers against P&L by rank.

For detailed workflows and session findings, use the docs in `docs/2026/`.
