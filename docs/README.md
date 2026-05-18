# Easy ORB Strategy — Docs

Primary documentation for the Easy ORB Strategy and Easy 0DTE Strategy. These docs match the references used by the strategy package.

## Index (primary docs)

| Doc | Purpose |
|-----|---------|
| **Strategy.md** | ORB strategy overview and timing |
| **ProcessFlow.md** | End-to-end daily workflow (ORB + optional 0DTE); **0DTE path** Signal Collection → options execution (Long + Short) table and key details |
| **Risk.md** | Position sizing, capital allocation, risk controls; 0DTE path (Convex, ranking, tiers, Hard Gate, execution) |
| **Alerts.md** | Telegram alerts and what they mean; Signal Collection and 0DTE Execution alert content (CALL/PUT) |
| **Settings.md** | Configuration (where to change what) |
| **Data.md** | Data sources, storage, and flow |
| **OAuth.md** | E*TRADE OAuth/token flow (high level) |
| **Cloud.md** | Cloud deployment guide (shareable; placeholders); [Optimization Strategy](Cloud.md#cloud-deployment-optimization-strategy) |
| **CloudSecrets.md** | Project-specific: project ID, service URLs, deploy commands (easy-etrade-strategy) |
| **PrivateSecrets.md** | Credentials only (do not commit) |
| **ARCHITECTURE.md** | System architecture |
| **0DTETrendline.md** | Easy Trendline 0DTE architecture, lifecycle, telemetry, and exits |
| **PriorityOptimizer.md** | Priority Optimizer data tracks, execution snapshots, and retrieval workflow |
| **Firebase.md** | Firebase OAuth web app |
| **StrategyImprovementGuide.md** | E*TRADE integration improvements |
| **doc_elements/Sessions/2026/Feb19 Session/** | Feb 19 session: 0-signals fix, validation candle 7:00/7:15, checklists, log diagnosis |
| **doc_elements/Sessions/2026/Feb27 Session/** | Feb 27 session: merge-on-persist (no 0-signal overwrite), execution GCS load, ORB LONG rule breakdown; [SESSION_SUMMARY_FEB27_2026.md](doc_elements/Sessions/2026/Feb27%20Session/SESSION_SUMMARY_FEB27_2026.md), [SIGNAL_COLLECTION_EXECUTION_FLOW.md](doc_elements/Sessions/2026/Feb27%20Session/SIGNAL_COLLECTION_EXECUTION_FLOW.md) |
| **recovery/** | Folder structure and recovery notes |

## 0DTE path (Signal Collection → options execution)

Primary docs now document the full 0DTE path: **0DTE produces both Long (CALL) and Short (PUT)** signals **only after the same three validation rules as ORB Long / inverse Short** (Rev 00309 — no bypass). Combined list is ranked (**Range 30%** = morning **ORB range %** from capture, same as Convex). Execution is capped by `0DTE_MAX_POSITIONS` (repo default `6` in `configs/ORB0DTE.env`) plus combined options cap `MAX_TOTAL_OPTION_POSITIONS`. **Rev 00311–00312:** **ORB range %** is one value end-to-end: ORB capture → `ORBData.orb_range_pct` → Convex → 0DTE priority → ORB SO priority (current base 2% + soft range-penalty layer) → Opening Bar Protection. **Rev 00302:** Convex logging (`CONVEX_REJECT`, etc.). Key locations:
- **ProcessFlow.md**: Table "0DTE Strategy — From Signal Collection List to Execution and Monitoring" (steps 1–9) and "Key details from 0DTE docs"
- **Strategy.md**: 0DTE Signal Collection (Long and Short), Convex 8 criteria, Risk Management, link to ProcessFlow and easy0DTE
- **Risk.md**: Paragraph "0DTE Options path (Long and Short)" with Convex, ranking, tiers, Hard Gate
- **ARCHITECTURE.md**: "0DTE Integration Flow" diagram and `prime_0dte_strategy_manager.py` description
- **easy0DTE/docs/Strategy.md**, **easy0DTE/docs/README.md**: Full 0DTE strategy details (Convex, strategy types, exit framework, position sizing tiers)

## Source of truth

The authoritative behavior is the code in:
- `modules/`
- `easy0DTE/modules/`
- `easyTrendline/`


