# Alert System User Guide
## Easy ORB Strategy - Complete Alert System Documentation

**Last Updated**: May 14, 2026  
**Version**: Rev 00332 (**May 11, 2026 — ORB 0DTE two-stage durability in Telegram**): Easy ORB 0DTE Options Execution HTML adds **`execution_durable`** (Stage A: broker path + monitor + first quote), **`exit_grade_durable`** (Stage B: exit-grade marks + good ticks), **`monitored_but_degraded`**; durable follow-up rows include **`quote_grade`** and **`exit_grade_ready`** so monitored-but-non–exit-grade positions (e.g. cached marks) do not read as “missing.” Related Cloud grep: **`ORB_0DTE_EXECUTION_DURABLE_CONFIRMED`**, **`ORB_0DTE_EXIT_GRADE_DURABLE_CONFIRMED`**, **`ORB_0DTE_STRATEGY_TYPE_PROPAGATED`**, **`ORB_0DTE_STRATEGY_TYPE_MISSING_FIXED`**, overextension **`ORB_0DTE_OVEREXTENSION_*`**, chain fallback **`ORB_0DTE_CHAIN_HEALTH_FALLBACK_*`**, spread degraded exits **`ORB_OPTIONS_EXIT_DEFERRED_AUDIT`**, **`ORB_OPTIONS_FORCED_DEGRADED_EXIT`**. **May 14, 2026:** add **`ORB_0DTE_SELECTOR_FULL_REPLAY`** (structured JSON on terminal selector rejects) and **`SO_CONTINUATION_VS_EXTENSION_BIAS`** (pairs with **`SO_RANK_BREAKDOWN`** — SO observability only). Prior: Rev 00331 (**ORB 0DTE durability bucket alert refresh** — May 7, 2026): Easy ORB 0DTE Options Execution alert now reports explicit durability buckets (`durable_executed`, `submitted_pending_confirmation`, `monitoring_active_not_yet_durable`, `chain_health_failed`, `selector_failed`, `durability_failed`, `broker_submit_failed`, plus `execution_attempts_total` / `submitted_total` / `pending_confirmation_total` / `true_failed_total`). When orders submitted but not yet durable, the alert renders **`⏳ ORB 0DTE orders submitted; awaiting durable confirmation`** instead of **`NO ... TRADES EXECUTED`**. New telemetry markers: **`ORB_0DTE_DURABILITY_ALERT_BUCKETS`**, **`ORB_0DTE_DURABILITY_RECONCILE_START`**, **`ORB_0DTE_DURABILITY_RECONCILE_RESULT`** (delayed durability reconciliation pass). Prior: Rev 00330 (**Trendline close/exec format refresh + list parity**) + May 4, 2026 cadence refresh (**EOD flatten** `flatten_all_paths_for_eod_scheduler` + POST `/api/end-of-day-report` same-process dedupe documented in §10); Trendline execution header `Trendline Options Execution`, close `TRENDLINE OPTION CLOSED`; 0DTE watchlist counts dynamic from `0dte_list.csv`. Earlier: Rev 00329 Tier-1 Red Day + selector diagnostics; Rev 00328 execution diagnostics; Rev 00321 EOD trio format; Rev 00318 pipeline block; Rev 00292 Convex diagnosis; Rev 00260 EOD single source.
**Purpose**: User guide for the integrated alert system: **ORB (ETF)**, **Easy ORB 0DTE (options)**, **Easy Trendline 0DTE (options)**, and **OAuth**. Includes Telegram setup and alert types. (Easy Collector is separate; not detailed here.)

**⚠️ Note**: For sensitive deployment-specific information (Telegram bot tokens, chat IDs), see [PrivateSecrets.md](PrivateSecrets.md). For service URLs and project-specific cloud data, see [CloudSecrets.md](CloudSecrets.md).

---

## 📋 **Table of Contents**

1. [Alert System Overview](#alert-system-overview)
2. [Telegram Setup Guide](#telegram-setup-guide)
3. [ORB Strategy Alerts](#orb-strategy-alerts)
4. [0DTE Strategy Alerts (Easy ORB 0DTE)](#0dte-strategy-alerts)
5. [Easy Trendline 0DTE Alerts](#easy-trendline-0dte-alerts)
6. [OAuth System Alerts](#oauth-system-alerts)
7. [Alert Configuration](#alert-configuration)
8. [Daily Alert Flow](#daily-alert-flow)
9. [Alert Formatting](#alert-formatting)
10. [Troubleshooting](#troubleshooting)

---

## 🚨 **Alert System Overview**

The integrated app sends Telegram alerts for **three separate traded ledgers** plus OAuth:

1. **Easy ORB (ETF account)**: Standard Order execution, stealth exits, 15-minute ETF portfolio health (emergency / weak day), scheduled **ETF EOD** report body (`send_end_of_day_report` — header **Easy ORB — END-OF-DAY (ETF account)**).
2. **Easy ORB 0DTE (options account)**: Options execution batch, per-position exits, partials, runners, demo `MockOptionsExecutor` open/close **system** alerts, scheduled **options EOD** (`send_options_end_of_day_report` — **Easy ORB 0DTE — END-OF-DAY**). Headers in Telegram HTML messages use the **`Easy ORB 0DTE`** prefix so they are not confused with Trendline.
3. **Easy Trendline 0DTE (dedicated options ledger)**: Trendline fill/exit alerts (dedicated Trendline senders), optional monitor heartbeat alerts, and **Trendline scheduled EOD** (separate from ORB 0DTE). See [0DTETrendline.md](0DTETrendline.md).
4. **OAuth System**: Token expiry and renewal (independent delivery path where noted).

### **Core Features**

- **Multi-Channel Delivery**: Telegram notifications with HTML formatting
- **Rich Formatting**: Emoji-enhanced messages with structured data
- **Dual Timezone Support**: All alerts display both PT and ET times with AM/PM format
- **Intelligent Throttling**: Prevents alert spam while maintaining critical notifications
- **Source Tracking**: Clear identification of alert source
- **Trade ID Formatting**: Shortened trade IDs for cleaner format
- **Enhanced Execution Alerts**: Bold formatting for key metrics
- **0DTE execution funnel** (Rev 00318): Telegram **0DTE pipeline (this run)** block; Cloud grep **`SO_PIPELINE`** / **`0DTE_DEMO | synthetic_chain`**

### **Alert Delivery**

All alerts are delivered via **Telegram** using HTML formatting. The system supports:
- Real-time trade notifications
- System status updates
- Performance summaries
- Error and warning alerts
- OAuth token management alerts

---

## 📱 **Telegram Setup Guide**

### **Step 1: Create a Telegram Bot**

1. Open Telegram and search for **@BotFather**
2. Start a conversation with BotFather
3. Send the command: `/newbot`
4. Follow the prompts to:
   - Choose a name for your bot (e.g., "Easy ORB Strategy Alerts")
   - Choose a username for your bot (must end in `bot`, e.g., `easy_orb_alerts_bot`)
5. BotFather will provide you with a **Bot Token** (e.g., `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)
6. **Save this token** - you'll need it for configuration

### **Step 2: Get Your Chat ID**

1. Open Telegram and search for **@userinfobot**
2. Start a conversation with @userinfobot
3. The bot will reply with your **Chat ID** (e.g., `123456789`)
4. **Save this Chat ID** - you'll need it for configuration

**Alternative Method** (if @userinfobot doesn't work):
1. Create a group chat or use an existing one
2. Add your bot to the group
3. Send a message in the group
4. Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
5. Look for the `"chat":{"id"` field in the response
6. Use the **negative number** (e.g., `-123456789`) for group chats

### **Step 3: Configure Telegram Credentials**

#### **For Local Development**

1. Navigate to `secretsprivate/` directory
2. Copy `telegram.env.template` to `telegram.env`
3. Edit `telegram.env` and add your credentials:
   ```bash
   TELEGRAM_BOT_TOKEN=your_bot_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   TELEGRAM_ENABLED=true
   ```
4. **Important**: `telegram.env` is gitignored - never commit it to version control

#### **For Cloud Deployment (GCP)**

Store credentials in Google Cloud Secret Manager:

```bash
# Store bot token
echo -n "your_bot_token_here" | gcloud secrets create telegram-bot-token --data-file=-

# Store chat ID
echo -n "your_chat_id_here" | gcloud secrets create telegram-chat-id --data-file=-
```

The system will automatically load these credentials in production.

### **Step 4: Test Your Setup**

1. Start the trading system
2. You should receive a test alert or the Good Morning alert
3. If you don't receive alerts, check:
   - Bot token is correct
   - Chat ID is correct
   - Bot is not blocked
   - System logs for errors

### **Step 5: Configure Alert Preferences**

Edit `configs/Alerts.env`:

```bash
# Enable Telegram alerts
TELEGRAM_ALERTS_ENABLED=true

# Rate limiting (prevent spam)
TELEGRAM_MAX_MESSAGES_PER_MINUTE=20
TELEGRAM_RATE_LIMIT_ENABLED=true
TELEGRAM_ALERT_COOLDOWN_SECONDS=60

# Alert types to receive
TELEGRAM_ALERT_TYPES=entry,exit,error,performance,daily_summary,system_status
```

### **Troubleshooting Telegram Setup**

**Issue**: No alerts received
- ✅ Verify bot token is correct
- ✅ Verify chat ID is correct
- ✅ Check that bot is not blocked
- ✅ Review system logs for errors
- ✅ Test bot manually: `https://api.telegram.org/bot<TOKEN>/getMe`

**Issue**: Alerts received but formatting is broken
- ✅ Check that HTML formatting is enabled (default)
- ✅ Verify message doesn't contain invalid HTML
- ✅ System will auto-fallback to plain text if HTML fails

**Issue**: Too many alerts (spam)
- ✅ Adjust `TELEGRAM_MAX_MESSAGES_PER_MINUTE` in config
- ✅ Enable `TELEGRAM_RATE_LIMIT_ENABLED`
- ✅ Increase `TELEGRAM_ALERT_COOLDOWN_SECONDS`

---

## 📊 **ORB Strategy Alerts**

### **1. Good Morning Alert (5:30 AM PT / 8:30 AM ET)**

**Trigger**: Cloud Scheduler at 5:30 AM PT daily  
**Purpose**: System status check and token validation  
**Content**:
- Token status (valid/expired)
- Configuration mode (Demo/Live)
- System health check
- Trading readiness status

**Features**:
- Time validation: Only sends between 5:30-5:35 AM PT
- Deduplication: One alert per day maximum (GCS-based)
- Protection: Rejects calls outside valid window

**Example**:
```
====================================================================

🌅 <b>Good Morning</b> | 🎮 DEMO Mode
          Time: 05:30 AM PT (08:30 AM ET)

✅ <b>System Status:</b>
          • OAuth Tokens: VALID ✅
          • Trading Mode: DEMO
          • System: READY

📊 <b>Next:</b> ORB Capture at 6:45 AM PT
          (Opening Range Breakout data collection)

====================================================================
```

### **2. ORB Capture Complete (6:45 AM PT / 9:45 AM ET)**

**Trigger**: After ORB capture completes (6:45 AM PT)  
**Purpose**: Confirmation of opening range capture  
**Content**:
- Number of symbols captured (dynamic merged ORB + 0DTE universe)
- Capture method (E*TRADE batch quotes only — broker-only, no third-party fallback)
- Processing time
- Any errors (no fallback; see Data.md for broker-only data source)
- Symbol count breakdown

**Example**:
```
====================================================================

✅ <b>ORB Capture Complete</b>
          Time: 06:45 AM PT (09:45 AM ET)

📊 <b>Capture Summary:</b>
          • Symbols Captured: <dynamic>
          • Method: E*TRADE Batch Quotes
          • Processing Time: 2.3 seconds

📈 <b>Next:</b> Signal Collection at 7:30 AM PT
          (Trade signal generation)

====================================================================
```

### **3. Trade Signal Collection (7:30 AM PT / 10:30 AM ET)**

**Trigger**: After signal collection and rules confirmation completes (7:30 AM PT)  
**Purpose**: Single alert showing final confirmed trade lists (after all rules and risk management)  
**Content**:
- **SO Signal Collection**: Final confirmed SO trades ready for execution
  - Number of confirmed SO trades (typically 6-15, or 0)
  - All rules and risk management applied
  - **7:30 Cutoff Enforcement (Rev 00330):** the pending SO candidates are revalidated at 7:30 using **fresh quotes** and kept only if `current_price_now >= orb_high * 1.001` (+0.1% buffer). Symbols failing the cutoff are removed before ranking/risk/execution.
  - Final execution-ready list (SO list is long-only)
- **0DTE Signal Collection**: Final confirmed 0DTE options trades ready for execution (if enabled)
  - Shows **CALL (Long)** and **PUT (Short)** breakdown
  - Shows ranked 0DTE candidate context and pipeline counts
  - **0DTE Pipeline diagnostics**: `candidates`, `convex`, `hard_gate`, `pending_exec`
  - Final 0DTE execution-ready set is the `pending_exec` list after Convex + Hard Gate + ranking cap
- When there are **0 signals**, the alert may include a **diagnostic reason** (`zero_signals_reason`) to help troubleshoot (e.g. validation candle data not available, all NEUTRAL, volume color, ORB data, rule rejection). See [CLOUD_LOGS_0_SIGNALS_FEB19.md](doc_elements/Sessions/2026/Feb19%20Session/CLOUD_LOGS_0_SIGNALS_FEB19.md) for ORB log diagnosis. **0DTE 0 signals:** When Convex filter rejects all, logs include check-by-check failure counts and grep-friendly `CONVEX_FILTER | 0_eligible | total=N | top_failures: ...` — see [SESSION_SUMMARY_FEB26_2026.md](doc_elements/Sessions/2026/Feb26%20Session/SESSION_SUMMARY_FEB26_2026.md).

**Note**: SO list is long-only and execution-ready at alert time. 0DTE section includes Long (CALL) and Short (PUT) ranked candidates plus pipeline diagnostics; `pending_exec` is the final execution-ready set. There is no separate 0DTE Signal Collection alert—0DTE is included in this single Trade Signal Collection alert.

**Example**:
```
====================================================================

📊 <b>Trade Signal Collection</b>
          Time: 07:30 AM PT (10:30 AM ET)

✅ <b>SO Signals (ORB Strategy):</b>
          • Confirmed Trades: 8
          • All rules applied: ✅
          • Ready for execution: ✅

✅ <b>0DTE Signals (Options Strategy):</b>
          • Confirmed Trades: 3
          • All rules applied: ✅
          • Ready for execution: ✅

📈 <b>Next:</b> Execution at 7:30 AM PT

====================================================================
```

### **4. Standard Order Execution (7:30 AM PT / 10:30 AM ET)**

**Trigger**: After batch execution completes (7:30 AM PT)  
**Purpose**: Detailed execution summary with enhanced formatting  
**Content**:
- Number of trades executed
- Total capital deployed
- Capital efficiency percentage
- Position details for each trade:
  - **Symbol** (e.g., QQQ, SPY)
  - **Quantity** (shares)
  - **Entry Price**
  - **<b>Rank #X</b>** (bold priority rank)
  - **<b>Priority Score: 0.856</b>** (bold priority score)
  - **<b>Confidence: 85%</b>** (bold confidence)
  - **<b>Momentum: 75/100</b>** (bold momentum)
  - **Trade ID**: Shortened format (e.g., `DEMO_QQQ_260105_485_488_c_704400`)

**Example**:
```
====================================================================

✅ <b>Standard Order Execution</b>
          Time: 07:30 AM PT (10:30 AM ET)

📊 <b>Execution Summary:</b>
          Trades Executed: 6
          Capital Deployed: $792.50 (88.1%)
          Capital Efficiency: 88.1%

📈 <b>Positions:</b>
          • QQQ - 12 shares @ $42.50
            <b>Rank #1</b> | <b>Priority Score: 0.856</b>
            <b>Confidence: 85%</b> | <b>Momentum: 75/100</b>
            Trade ID: DEMO_QQQ_260106_485_488_c_704400

          • SPY - 8 shares @ $485.00
            <b>Rank #2</b> | <b>Priority Score: 0.823</b>
            <b>Confidence: 82%</b> | <b>Momentum: 68/100</b>
            Trade ID: DEMO_SPY_260106_485_488_c_704401

====================================================================
```

### **5. ETF Portfolio Health Check (Every 15 Minutes)**

**Trigger**: Every 15 minutes during the trading loop (after SO execution time is recorded — same loop as position monitoring).  
**Scope**: **Easy ORB ETF / stealth positions only** (`stealth_trailing.check_portfolio_health_for_emergency_exit`). Does **not** include Easy ORB 0DTE options or Easy Trendline 0DTE (those have separate monitoring paths).

**Purpose**: Detect weak / bad days and batch-close ETF demo positions; send **at most one** emergency and **one** weak-day Telegram per day (duplicate sends suppressed while 15m loop continues).

**Delivery**: `alert_manager.send_telegram_alert(...)` (not `send_system_alert`). Titles in code: **Easy ORB (ETF) — BAD DAY — EMERGENCY EXIT** and **Easy ORB (ETF) — WEAK DAY DETECTED**.

**Behavior**:
- **EMERGENCY** (`CLOSE_ALL`): Batch close all flagged ETF positions; red-flag list + action line in Telegram.
- **WARNING** (`CLOSE_WEAK`): Batch close weak ETF positions; warnings + remaining position count.
- **OK**: Log only (`HEALTH CHECK PASSED`).

**Red flags** (from stealth / portfolio health): win rate, avg P&L, momentum, peaks, all losers — see `prime_stealth_trailing_tp` / health check implementation for exact thresholds.

**Example (EMERGENCY — shape matches code)**:
```
🚨 <b>Easy ORB (ETF) — BAD DAY — EMERGENCY EXIT</b>

📊 <b>Red Flags:</b>
  ❌ (dynamic flags from health check)

🛡️ <b>Action:</b> Closed N positions
💰 Exited early to preserve capital
```

### **6. Position Exit Alerts**

#### **Individual Exits**

**Trigger**: When individual position closes  
**Purpose**: Detailed exit information  
**Content**:
- Exit reason (trailing stop, breakeven, rapid exit, etc.)
- Entry and exit prices
- P&L (absolute and percentage)
- Hold time
- Peak price reached
- Trade ID (shortened format)

**Example**:
```
====================================================================

🔄 <b>Position Closed</b>
          Time: 09:15 AM PT (12:15 PM ET)

📉 <b>QQQ - 12 shares</b>
          Entry: $42.50 → Exit: $43.15
          P&L: +$7.80 (+1.53%)
          Hold Time: 1h 45m
          Peak: $43.25 (+1.76%)
          Exit Reason: Trailing Stop
          Trade ID: DEMO_QQQ_260106_485_488_c_704400

====================================================================
```

#### **Aggregated Exits (Batch Closes)**

**Trigger**: Batch closes (EOD, emergency, weak day)  
**Purpose**: ONE alert for all positions closed  
**Content**:
- Summary of exit reasons
- Total P&L
- Number of positions closed
- Individual position details (if space permits)
- Prevents duplicate notifications

**Example**:
```
====================================================================

🔄 <b>End of Day Close</b>
          Time: 12:55 PM PT (03:55 PM ET)

📊 <b>Summary:</b>
          Positions Closed: 6
          Total P&L: +$45.23 (+5.7%)

📈 <b>Positions:</b>
          • QQQ: +$12.50 (+2.1%) - Trailing Stop
          • SPY: +$8.75 (+1.8%) - Breakeven
          • TQQQ: +$15.20 (+3.2%) - EOD Close
          • SOXL: +$4.50 (+0.9%) - EOD Close
          • UPRO: +$2.28 (+0.5%) - EOD Close
          • NEBX: +$2.00 (+0.3%) - EOD Close

====================================================================
```

### **7. Rapid Exit Alerts**

**Trigger**: When trades rapidly exit (no momentum or reversal)  
**Purpose**: Notification of early exits to prevent losses  
**Content**:
- Exit reason (NO_MOMENTUM or IMMEDIATE_REVERSAL)
- Time held
- Entry and exit prices
- P&L
- Peak price reached
- Trade ID

**Example**:
```
====================================================================

🚨 <b>RAPID EXIT - No Momentum</b>

⏰ <b>Time Held:</b> 18 minutes

📉 <b>12 QQQ @ $42.50</b> • <b>$510.00</b>
  • <b>Current P&L:</b> -0.15% (-$0.77)
  • <b>Peak:</b> $42.63 (+0.30%)
  • Trade ID: DEMO_QQQ_260106_485_488_c_704400

🚨 <b>Exit Reason:</b>
  Peak movement <+0.3% after 15 minutes
  Trade shows no momentum - exiting to limit loss

💡 <b>Action:</b> Position closed at -0.15%
   Early exit to prevent further loss

====================================================================
```

### **8. Red Day Alert**

**Trigger**: When Red Day is detected during signal collection  
**Purpose**: Notification that Red Day long-side execution is blocked due to bearish market conditions  
**Content**:
- Red Day reason (pattern description)
- Market conditions (RSI, volume, MACD, etc.)
- Action taken (**ORB Long blocked, 0DTE Long/CALL non-Tier-1 blocked, 0DTE Long/CALL Tier-1 allowed, 0DTE Short/PUT allowed**)
- Signal collection summary
- Strategy impact
- **Deduplication**: Sent once per trading day (prevents repeated Red Day spam alerts).

**Example**:
```
====================================================================

🚨 <b>RED DAY DETECTED</b> | 🎮 DEMO Mode
          Time: 07:30 AM PT (10:30 AM ET)

🔍 <b>Red Day Reason:</b>
          Oversold market conditions detected (RSI < 40 in 60%+ symbols)

📊 <b>Market Conditions:</b>
          • Oversold (RSI under 40): 65%
          • Overbought (RSI above 80): 5%
          • Weak Volume (below 1.0x): 45%
          • Avg RSI: 38.5
          • Avg Volume: 0.85x

💰 <b>Action Taken:</b>
          • ORB Long trades: <b>BLOCKED</b> (capital preserved)
          • 0DTE Long/CALL < Tier 1: <b>BLOCKED</b>
          • 0DTE Long/CALL Tier 1: <b>CONTINUING</b>
          • 0DTE Short/PUT: <b>CONTINUING</b> (bearish setups allowed)

📊 <b>Signal Collection:</b>
          • Symbols Scanned: <dynamic>
          • ORB Signals: 8 (blocked from execution)
          • 0DTE Signals: 3 (Short/PUT path only on Red Day)

====================================================================
```

### **9. Holiday Alert**

**Trigger**: When market is closed or low-volume day detected (5:30 AM PT, instead of Good Morning)  
**Purpose**: Notification that trading is skipped  
**Content**:
- Holiday name and date
- Skip reason: **MARKET_CLOSED** (bank holiday, market closed; emoji 🏖️) or **LOW_VOLUME** (market open but low volume, e.g. Halloween; emoji 🎃)
- Next trading day
- System status

**Example**:
```
====================================================================

🏖️ <b>Market Holiday</b>
          Time: 05:30 AM PT (08:30 AM ET)

📅 <b>Holiday:</b> Christmas Day
          Reason: MARKET_CLOSED

⏭️ <b>Next Trading Day:</b> Thursday, December 26, 2025

✅ <b>System Status:</b>
          • Trading: SKIPPED
          • System: IDLE
          • Next Alert: Tomorrow at 5:30 AM PT

====================================================================
```

### **10. End-of-Day Reports (4:05 PM ET / 1:05 PM PT)**

**Trigger**: Cloud Scheduler job **`end-of-day-report`** → **`POST /api/end-of-day-report`** only (Rev 00260). Actual clock time follows the GCP schedule (documented baseline **4:05 PM ET** / **1:05 PM PT** weekdays).  
**Before Telegram**: Handler invokes **`PrimeTradingSystem.flatten_all_paths_for_eod_scheduler()`** (ORB ETF + ORB 0DTE + Trendline). With **`skip_if_already_flattened_today`** (default), if the main loop already ran the same flatten after **12:55** PT in this process, the handler logs **`EOD_SCHEDULER_FLATTEN | skip | same_process_already_flattened_today`** and skips redundant closes.  
**Deduplication**: GCS markers per report type (ORB ETF demo, ORB 0DTE options, Trendline EOD, etc.).

**Three account-level summaries** (when components are initialized):

| # | Ledger | Method / alert body header |
|---|--------|----------------------------|
| 1 | **Easy ORB (ETF)** | `_send_demo_eod_summary` / `_send_live_eod_summary` → `send_end_of_day_report` — **🛃 END-OF-DAY ORB \| 🌾** |
| 2 | **Easy ORB 0DTE (options)** | `send_options_end_of_day_report` — **🏦 END-OF-DAY ORB 0DTE \| 🎮** |
| 3 | **Easy Trendline 0DTE (options)** | `/api/end-of-day-report` → `send_trendline_end_of_day_telegram` (raw body + GCS dedup like ORB 0DTE EOD) — **💎 END-OF-DAY TREND 0DTE \| 🎲** |

**Content** (all three reports): shared structure with:
- `Time: {pt_time} ({et_time})`
- `📈 P&L (TODAY): | {mode} Mode`
- `🎖️ P&L (WEEK M-F):`
- `💎 Account Balances (All Time):`
- `📅 Report Date: {YYYY-MM-DD}`

**Note**: The main trading loop may **flatten all three paths** in **`SO_ETF_EOD_CLOSE_*`** PT (default **12:55**–**12:56**) via **`flatten_all_paths_for_eod_scheduler()`** — same orchestration as the HTTP handler. That is **position management / ledger flatten**, separate from the scheduled **Telegram** EOD trio at **`/api/end-of-day-report`**. Trendline’s scheduled summary uses the **`💎 END-OF-DAY TREND 0DTE`** header from that endpoint only.

**Example (EOD ORB — exact format)**:
```
====================================================================

🛃 END-OF-DAY ORB | 🌾
          Time: 01:05 PM PT (04:05 PM ET)

📈 P&L (TODAY): | DEMO Mode
          +5.7% +$45.23
          Win Rate: 66.7% • Total Trades: 6
          Wins: 4 • Losses: 2
          Profit Factor: 2.10
          Average Win: $12.50
          Average Loss: -$5.20
          Best Trade: +$15.20
          Worst Trade: -$2.00

🎖️ P&L (WEEK M-F):
          +12.3% +$98.50
          Win Rate: 62.5% • Total Trades: 24
          Profit Factor: 1.80

💎 Account Balances (All Time):
          +45.2% +$362.00
          $1,162.00
          Win Rate: 58.3% • Total Trades: 120
          Profit Factor: 1.60
          Wins: 70 • Losses: 50

📅 Report Date: 2026-04-09

====================================================================
```

### **11. ORB Capture Failed Alert**

**Trigger**: When ORB capture fails for all symbols  
**Purpose**: Notification of data collection failure  
**Content**:
- Number of symbols attempted
- Failure reason
- Recovery actions taken
- Next steps

---

## 🎯 **0DTE Strategy Alerts**

**Branding**: Telegram HTML for this path uses **`Easy ORB 0DTE`** in headers so option fills/exits/EOD are distinct from **Easy Trendline 0DTE** (below).

When `ENABLE_0DTE_STRATEGY=true` is set (typically via `configs/ORB0DTE.env` or process env), the Easy ORB 0DTE options stack is active alongside ORB ETF execution.

### **1. 0DTE ORB Capture (Integrated with ORB Capture Complete)**

**Trigger**: After ORB capture completes (6:45 AM PT)  
**Purpose**: Same as ORB Capture Complete; when 0DTE is enabled, the **single** ORB Capture Complete alert includes 0DTE symbol **counts**.  
**Content** (in the one ORB Capture Complete alert):
- **Opening Range Capture**: ORB Strategy symbols captured and active counts
- **0DTE ORB Capture** (when 0DTE enabled): 0DTE symbols captured count and active count, same capture duration
- Detailed 0DTE symbol ORB data (e.g. SPX/QQQ/SPY high/low) is **not** shown in this alert (simplified per Rev 00186)

**Note**: There is no separate "0DTE ORB Capture" alert. The main **ORB Capture Complete** alert includes both ORB Strategy and 0DTE Strategy **counts** when 0DTE is enabled. Watchlist path for 0DTE symbols: `data/watchlist/0dte_list.csv`.

### **2. 0DTE Options Signal Collection (Integrated with Trade Signal Collection)**

**Trigger**: At 7:30 AM PT, same time as Trade Signal Collection  
**Purpose**: Summary of qualified 0DTE options signals; **no separate alert**—0DTE is included in the **Trade Signal Collection** alert. The 0DTE path from Signal Collection to options execution: [ProcessFlow.md](ProcessFlow.md#0dte-strategy--from-signal-collection-list-to-execution-and-monitoring), [Strategy.md](Strategy.md).  
**Content** (in the single Trade Signal Collection alert when 0DTE enabled):
- SO (ORB) confirmed trades count and list (long-only)
- 0DTE confirmed trades: **CALL (Long)** and **PUT (Short)** breakdown, counts, and **full symbol lists** for all Convex-qualified 0DTE signals that passed Hard Gate pre-validation (not truncated). Header shows `0DTE Options Ready (N symbols)`, followed by separate CALL and PUT lines.
- All rules and risk management applied; final execution-ready lists for both

**Integration**: There is only one Signal Collection alert. It shows both ORB Strategy (SO) and 0DTE Strategy results. A deprecated separate `send_options_signal_collection_alert` exists in code but is not used; the unified `send_so_signal_collection()` sends the combined alert.

### **3. Easy ORB 0DTE — Options Execution Alert**

**Trigger**: After Easy ORB 0DTE options execution completes (7:30 AM PT, after ORB SO execution). Executed trades are from the post–Convex / Hard Gate queue (LONG → CALL, SHORT → PUT); path from collection to execution: [ProcessFlow.md](ProcessFlow.md#0dte-strategy--from-signal-collection-list-to-execution-and-monitoring).  
**Purpose**: Execution summary plus an explicit **pipeline funnel** so collection row counts are not confused with execution attempts.  
**Method**: `send_options_execution_alert` in `modules/prime_alert_manager.py`.

**Content**:
- **Header**: **`🔮 Easy ORB 0DTE — Options Execution`** `| DEMO or LIVE Mode` and time (PT and ET)
- **🎙️ Easy ORB 0DTE pipeline (this run)** (Rev 00318 + Rev 00331 durability + Rev 00332 two-stage / quote rows):
  - **Watchlist** (`0dte_list.csv` size)
  - **Collection underlyings** — CALL+PUT rows from Signal Collection scan
  - **Convex-qualified** (after strategy / Convex filter)
  - **Hard gate → execution queue**
  - **Execution attempts** (chain + momentum + strategy handling)
  - **Submitted total** (broker accepted; sums durable + pending + monitoring-not-yet-durable)
  - **Pending confirmation** (not durable yet — surfaces broker timing lag, not failure)
  - **Dropped before Hard Gate** (Convex + dedupe + top-N cap)
  - **True failed/rejected** (execution step — true rejects + durability failures)
  - Optional **Avg Momentum** when momentum data present
- **Durability classification (Rev 00331)**: counters come from canonical buckets `durable_executed`, `submitted_pending_confirmation`, `monitoring_active_not_yet_durable`, `chain_health_failed`, `selector_failed`, `durability_failed`, `broker_submit_failed` (see telemetry **`ORB_0DTE_DURABILITY_ALERT_BUCKETS`**). A delayed durability reconciliation pass (**`ORB_0DTE_DURABILITY_RECONCILE_START`** / **`_RESULT`**) runs before final classification so submitted-but-not-yet-durable orders are not counted as rejected.
- **Two-stage durability + quote row (Rev 00332, May 11)**: summary lines **`Execution durable`**, **`Exit-grade durable`**, **`Monitored, degraded / pending exit-grade`**; per durable row **`quote_grade`** (e.g. `cached_recent`, `exit_grade`) and **`exit_grade_ready`** so Stage A (execution path live) is visible when Stage B (exit-grade marks) is still pending.
- **When durable trades fill**: **`💼 Easy ORB 0DTE — options executed:`** count, per-position blocks (debit spread, credit spread, lotto as applicable) with rank/score, delta, strikes, premiums, **% of account**, Trade ID; pending rows still appear under **`⏳ Submitted, awaiting durable confirmation`** when present.
- **Capital Deployment**: deployed / account (%)
- **When orders submitted but not yet durable** (Rev 00331): **`⏳ ORB 0DTE orders submitted; awaiting durable confirmation`** with `Execution attempts`, `Submitted total`, `Pending confirmation`, optional `Why fewer names than Signal Collection?` note, true rejection reason rollups, and the **`⏳ Submitted, awaiting durable confirmation`** per-symbol block (symbol, trade ID, monitor state). System status line: **`Monitoring active positions; durable confirmation pending`**.
- **When zero fills (and zero pending)**: **`NO EASY ORB 0DTE OPTIONS TRADES EXECUTED`** with **Actual rejection reasons** (grouped counts + sample symbols) and optional **Details (first N signals)**; optional note **Why fewer names than Signal Collection?** when collection underlyings > execution attempts (Convex / Hard Gate / earlier filters).
- **🧪 Chain health diagnostics block** (when chain-health failures present): retry count, relaxed pass count, prequeue extension rejects, execution count, plus per-failure-type histogram (`no_chain_data`, `empty_chain`, `no_valid_strikes`, `spread_too_wide`, `low_liquidity`, `API_error`, `timeout`, `unknown`).
- **Per-symbol execution rejects (Rev 00329)**: Text like **`SYMBOL: No debit spread found from chain/strike selector`** or **`SYMBOL: No ITM probability spread found from chain/strike selector`** means spread selection returned no valid pair (premium/delta/OTM/pairing/liquidity gates), not “ORB rules failed.”
- **Selector leg diagnostics (Rev 00329)**: On ITM spread failures, Cloud logs include `0DTE_SELECTOR_DIAG` plus `ITM_SPREAD_SELECTOR` with failure stage (for example: `long_leg_unavailable`, `short_leg_unavailable`, `long_leg_liquidity`, `short_leg_liquidity`) so leg-level root cause is explicit.
- After payoff guardrails, **`poor_payoff_profile`** remains separate from **`spread_selection_failed`**.

**Ops / logs**: Grep Cloud Logging for **`SO_PIPELINE`** (Standard Order path), **`SO_RANK_BREAKDOWN`**, **`SO_CONTINUATION_VS_EXTENSION_BIAS`**, **`0DTE_DEMO | synthetic_chain`** (DEMO synthetic chain generation), **`spread_selection_failed`**, **`No debit spread found`**, **`No ITM probability spread found`**, **`0DTE_SELECTOR_DIAG`**, **`ITM_SPREAD_SELECTOR`** for strike-selection root cause, **`ORB_0DTE_SELECTOR_FULL_REPLAY`** for full selector ladder / width-ladder rejects, **`ORB_0DTE_DURABILITY_ALERT_BUCKETS`** / **`ORB_0DTE_DURABILITY_RECONCILE_START`** / **`ORB_0DTE_DURABILITY_RECONCILE_RESULT`** for durability classification. May 11 additions: **`ORB_0DTE_EXECUTION_DURABLE_CONFIRMED`**, **`ORB_0DTE_EXIT_GRADE_DURABLE_CONFIRMED`**, **`ORB_0DTE_STRATEGY_TYPE_PROPAGATED`**, **`ORB_0DTE_OVEREXTENSION_SOFT_PENALTY`** / **`ORB_0DTE_OVEREXTENSION_ALLOWED_STRONG_CONTINUATION`** / **`ORB_0DTE_OVEREXTENSION_EXTREME_REJECT`**, **`ORB_0DTE_CHAIN_HEALTH_FALLBACK_*`**, **`ORB_OPTIONS_EXIT_DEFERRED_AUDIT`**, **`ORB_OPTIONS_FORCED_DEGRADED_EXIT`**.

**Example (with trades — shape varies by strategy type)**:
```
====================================================================

🔮 <b>Easy ORB 0DTE — Options Execution</b> | DEMO Mode
          Time: 07:30 AM PT (10:30 AM ET)

🎙️ <b>Easy ORB 0DTE pipeline (this run):</b>
          • <b>Watchlist</b> (0dte_list.csv): <dynamic>
          • <b>Collection underlyings</b> (CALL+PUT rows in Signal Collection): 34
          • <b>Convex-qualified</b> (strategy filter): 20
          • <b>Hard gate → execution queue</b>: 12
          • <b>Execution attempts</b> (chain + momentum + strategy handling): 11
          • <b>Submitted total</b> (broker accepted): 8
          • <b>Pending confirmation</b> (not durable yet): 0
          • <b>Dropped before Hard Gate</b> (Convex + dedupe + top-N cap): 14
          • <b>True failed/rejected</b> (execution step): 3

💼 <b>Easy ORB 0DTE — options executed:</b> 3
          (numbered debit spread / lotto / long lines with Rank, Delta, strikes, Trade ID)

💰 <b>Capital Deployment:</b>
          • <b>Deployed:</b> $1,250.00 / $5,000.00 (25.0%)

🛡️ <b>Monitoring:</b> All positions tracked (fast loop ~7s + shared backup)
====================================================================
```

**Example (orders submitted, not yet durable — Rev 00331)**:
```
====================================================================

🔮 <b>Easy ORB 0DTE — Options Execution</b> | DEMO Mode
          Time: 07:30 AM PT (10:30 AM ET)

🎙️ <b>Easy ORB 0DTE pipeline (this run):</b>
          • <b>Watchlist</b> (0dte_list.csv): <dynamic>
          • <b>Collection underlyings</b> (CALL+PUT rows in Signal Collection): 34
          • <b>Convex-qualified</b> (strategy filter): 20
          • <b>Hard gate → execution queue</b>: 12
          • <b>Execution attempts</b> (chain + momentum + strategy handling): 6
          • <b>Submitted total</b> (broker accepted): 3
          • <b>Pending confirmation</b> (not durable yet): 3
          • <b>Dropped before Hard Gate</b> (Convex + dedupe + top-N cap): 14
          • <b>True failed/rejected</b> (execution step): 3

⏳ <b>ORB 0DTE orders submitted; awaiting durable confirmation</b>

<b>Execution attempts:</b> 6
<b>Submitted total:</b> 3
<b>Pending confirmation:</b> 3

💡 <b>True rejection reasons (so far):</b>
          • <b>chain_health_failed</b>: 2 (e.g. LEN, AEHR)
          • <b>spread_selection_failed</b>: 1 (e.g. BLDR)

⏳ <b>Submitted, awaiting durable confirmation</b>:
          - MU (DEMO_MU_260507_..._c_725) — submitted_pending
          - IREN (DEMO_IREN_260507_61_62_c_725) — monitoring_active_not_yet_durable
          - SLV (DEMO_SLV_260507_..._c_725) — submitted_pending

📊 <b>System status:</b> Monitoring active positions; durable confirmation pending
====================================================================
```

### **4. Easy ORB 0DTE — Position Exit Alerts**

#### **Individual Exits**

**Trigger**: When an Easy ORB 0DTE options position closes (exit manager path).  
**Method**: `send_options_position_exit_alert` — header includes **`Easy ORB 0DTE — OPTION CLOSED`**.  
**Purpose**: Detailed exit information with real-time options P&L  
**Content**:
- Exit reason (profit target +60%, profit target +120%, hard stop, time stop, invalidation, EOD, etc.)
- Entry and exit prices (real options prices, not underlying)
- P&L (absolute and percentage) - based on actual options moves
- Hold time
- Peak value reached
- Strategy type (debit spread, long call, etc.)
- Trade ID (shortened format)

**Real-time contract marks**:
- Exit decisions use actual option premiums from the broker; open positions are polled on the **fast monitor** (default **~7s**, `ORB_0DTE_POSITION_MONITOR_INTERVAL_SEC`) with backoff under pressure, alongside broader **`ORB_OPTIONS_MONITOR_INTERVAL_SEC`** work (default **30s**)
- P&L reflects contract marks when quotes are available (strict modes: `OPTION_REQUIRE_LIVE_QUOTES` / `REQUIRE_LIVE_OPTION_DATA`)

#### **Aggregated Exits**

**Trigger**: Batch closes (EOD at 12:55 PM PT, emergency exits) for **Easy ORB 0DTE** book.  
**Method**: `send_options_aggregated_exit_alert` — header **`Easy ORB 0DTE — OPTIONS CLOSED`**.  
**Purpose**: ONE alert for all Easy ORB 0DTE options positions closed in that batch  
**Content**:
- Summary of exit reasons
- Total P&L (based on actual options prices)
- Number of positions closed
- Individual position details (if space permits)
- Strategy breakdown (debit spreads, long calls, etc.)

**Example**:
```
====================================================================

🔮 💠 <b>Easy ORB 0DTE — OPTIONS CLOSED</b> | DEMO Mode
          Time: 12:55 PM PT (03:55 PM ET)
          (example shape; reason line may show eod_close, etc.)

📊 <b>Summary:</b>
          Positions Closed: 3
          Total P&L: +$1,250.00 (+250.0%)

📈 <b>Positions:</b>
          • QQQ 628c: +$780.00 (+410.5%) - Profit Target +60%
          • SPY 485/486c: +$350.00 (+100.0%) - EOD Close
          • IWM 257c: +$120.00 (+137.1%) - EOD Close

====================================================================
```

### **5. Easy ORB 0DTE — Partial Profit Alert**

**Trigger**: When partial profit is taken (automated profit targets enabled).  
**Method**: `send_options_partial_profit_alert` — header prefixes **`Easy ORB 0DTE —`** (e.g. **FIRST TARGET EXIT**).  
**Purpose**: Notification of partial profit realization  
**Content**:
- Partial profit amount (based on actual options prices)
- Remaining position size
- Current P&L (real-time options prices)
- Profit target reached (+60% or +120%)
- Strategy type
- Trade ID

**Automated Profit Targets**:
- **First Target**: +60% → Sell 50% of position
- **Second Target**: +120% → Sell 25% of remaining position
- **Runner**: Trails remaining position until exit conditions

### **6. Easy ORB 0DTE — Runner Exit Alert**

**Trigger**: When runner position exits (after partial profits taken).  
**Method**: `send_options_runner_exit_alert` — header **`Easy ORB 0DTE — RUNNER EXIT`**.  
**Purpose**: Notification of runner exit  
**Content**:
- Runner exit P&L (based on actual options prices)
- Total position P&L (partial profits + runner)
- Exit reason (VWAP reclaim, ORB midpoint reclaim, time cutoff, etc.)
- Strategy type
- Trade ID

### **7. Easy ORB 0DTE — Options Health Check (implemented, not wired)**

**Method**: `send_options_health_check_alert` in `prime_alert_manager.py` (header **`Easy ORB 0DTE — PORTFOLIO HEALTH`**).

**Current code status**: As of this doc revision, **no caller** in the main trading loop invokes this method — options monitoring uses `OptionsTradingExecutor` / exit manager paths instead. The method exists for future or manual use; do not expect Telegram health alerts from it until wired.

### **8. Easy ORB 0DTE — Scheduled End-of-Day Report**

**Trigger**: Cloud Scheduler endpoint at 4:05 PM ET (1:05 PM PT) daily  
**Source**: Single source - Cloud Scheduler endpoint only (Rev 00260)  
**Deduplication**: GCS-based (prevents duplicate reports - Rev 00260)  
**Purpose**: Daily **Easy ORB 0DTE options** performance summary (separate ledger from ETF and Trendline).

**Note**: Triggered ONLY by Cloud Scheduler **`/api/end-of-day-report`** with the other EOD reports. GCS deduplication key includes mode (demo/live).  
**Content**:
- Total options P&L (based on actual options prices)
- Number of options trades executed
- Win rate (winning trades / total trades)
- Best and worst trades (with strategy types)
- Strategy breakdown:
  - Debit spreads: count, P&L, win rate
  - Long calls/puts: count, P&L, win rate
  - Momentum scalpers: count, P&L, win rate
  - ITM probability spreads: count, P&L, win rate
  - Lotto sleeves: count, P&L, win rate
- Average P&L per trade
- Capital efficiency (capital deployed / available capital)

**Example (EOD ORB 0DTE — exact format)**:
```
====================================================================

🏦 END-OF-DAY ORB 0DTE | 🎮
          Time: 01:05 PM PT (04:05 PM ET)

📈 P&L (TODAY): | DEMO Mode
          +0.00% +$0.00
          Win Rate: 0.0% • Total Trades: 0
          Wins: 0 • Losses: 0
          Profit Factor: 0.00
          Average Win: $0.00
          Average Loss: $0.00
          Best Trade: +$0.00
          Worst Trade: +$0.00

🎖️ P&L (WEEK M-F):
          +0.00% +$0.00
          Win Rate: 0.0% • Total Trades: 0
          Profit Factor: 0.00

💎 Account Balances (All Time):
          +0.00% +$0.00
          $5,000.00
          Win Rate: 0.0% • Total Trades: 0
          Profit Factor: 0.00
          Wins: 0 • Losses: 0

📅 Report Date: 2026-04-09

====================================================================
```

### **9. Demo: per-position system alerts (Easy ORB 0DTE)**

When `OptionsTradingExecutor` uses **`MockOptionsExecutor`** (demo), opening or closing a spread/lotto may send **`send_system_alert`** with titles such as:

- `🎮 DEMO | Easy ORB 0DTE | Debit Spread Opened` / `Lotto Sleeve Opened` / `Credit Spread Opened`
- `🎮 DEMO | Easy ORB 0DTE | Position Closed`

Body first line: **`Strategy: Easy ORB 0DTE (options)`**. These complement (do not replace) the batch **`send_options_execution_alert`** summary after the Step 5 execution loop.

---

## 📈 **Easy Trendline 0DTE Alerts**

**Enable**: `ENABLE_TRENDLINE_STRATEGY=true` (see `configs/Trendline0DTE.env` / runtime env). Uses the same **0dte_list.csv** / ORB capture universe as Easy ORB 0DTE for context, but a **separate demo ledger** (`TrendlineAccountManager`) and stealth exit engine (`TrendlineOptionsStealthEngine`).

**Delivery**:
- **Execution fills**: `send_trendline_options_execution_alert` (dedicated Trendline path, not generic cooldown path)
- **Position closes**: `send_trendline_position_exit_alert` (dedicated Trendline path)
- **Monitor alerts**: optional `send_system_alert` path, controlled by `TRENDLINE_MONITOR_ALERT_ENABLED` (default off)
- **Scheduled EOD** body is sent via **`send_trendline_end_of_day_telegram`** (`prime_alert_manager.py`, called from `main.py`) — same raw Telegram pipe as ORB ETF / ORB 0DTE EOD (starts with `====================================================================`), plus GCS `eod_markers/trendline_eod_sent_{date}_{mode}.txt` dedup.

| Event | Title pattern (DEMO) | Notes |
|--------|----------------------|--------|
| Fill | `🔮 Trendline Options Execution \| DEMO` | After successful `TrendlineOptionsExecutor` open |
| Exit (stealth / TP path) | `🔮 TRENDLINE OPTION CLOSED \| DEMO` | Includes enriched close line (pnl%, $pnl, qty/symbol/side, delta range, reason, balance) |
| Periodic monitor | `🔮 Easy Trendline 0DTE — Monitor \| DEMO` | Optional; disabled by default via `TRENDLINE_MONITOR_ALERT_ENABLED=false`; if enabled, throttled by `TRENDLINE_MONITOR_ALERT_INTERVAL_SEC` |
| Scheduled summary | `💎 END-OF-DAY TREND 0DTE \| 🎲` | From **`/api/end-of-day-report`** in `main.py` → **`send_trendline_end_of_day_telegram`**; same EOD section layout as ORB and ORB 0DTE |

**Labels in code**: `ALERT_LABEL_EASY_TRENDLINE_0DTE` in `modules/prime_alert_manager.py` (used by `prime_trading_system.py` for Trendline system alerts).

**Docs**: [0DTETrendline.md](0DTETrendline.md) for behavior, env keys, and telemetry.

**Snapshot observability note**: on each successful Trendline execution, the app also attempts an execution snapshot write to Priority Optimizer with `stage=trendline_options_executed` and `snapshot_strategy=easy_trendline_0dte` (`EXECUTION_FEATURE_SNAPSHOT_ATTEMPT` in logs).

**Example (EOD Trendline 0DTE — exact format)**:
```
====================================================================

💎 END-OF-DAY TREND 0DTE | 🎲
          Time: 01:05 PM PT (04:05 PM ET)

📈 P&L (TODAY): | DEMO Mode
          +0.00% +$0.00
          Win Rate: 0.0% • Total Trades: 0
          Wins: 0 • Losses: 0
          Profit Factor: 0.00
          Average Win: $0.00
          Average Loss: $0.00
          Best Trade: +$0.00
          Worst Trade: +$0.00

🎖️ P&L (WEEK M-F):
          +0.00% +$0.00
          Win Rate: 0.0% • Total Trades: 0
          Profit Factor: 0.00

💎 Account Balances (All Time):
          +0.00% +$0.00
          $5,000.00
          Win Rate: 0.0% • Total Trades: 0
          Profit Factor: 0.00
          Wins: 0 • Losses: 0

📅 Report Date: 2026-04-09

====================================================================
```

---

## 🔐 **OAuth System Alerts**

### **1. OAuth Token Expiry Alert (Midnight)**

**Trigger**: Cloud Scheduler at 9:00 PM PT (12:00 AM ET) daily  
**Purpose**: Alert when production token expires at midnight ET (sandbox deprecated — only production is used for data)  
**Delivery**: Direct Telegram API (works 24/7, independent of main trading system)  
**Independence**: Sends even when trading system is not actively running

**Example**:
```
====================================================================

⚠️ <b>OAuth Tokens Expired</b>
          Time: 09:00 PM PT (12:00 AM ET)

🚨 <b>Token Status:</b>
          E*TRADE tokens are <b>EXPIRED</b> ❌

🌐 <b>Public Dashboard:</b>
          https://easy-trading-oauth-v2.web.app

⚠️ Renew Production Token (used for all data and trading — Demo and Live)

👉 <b>Action Required:</b>
1. Visit the public dashboard
2. Click "Renew Production" (production token only; sandbox not used)
3. Enter access code (see PrivateSecrets.md)
4. Complete OAuth authorization
5. Token will be renewed and stored

====================================================================
```

### **2. OAuth Production Token Renewed**

**Trigger**: Successful production token renewal (via any management portal URL)  
**Purpose**: Confirmation when production tokens are renewed  
**Delivery**: Direct Telegram API call (works 24/7, independent of trading system)

### **3. OAuth Sandbox Token Renewed** *(deprecated)*

**Trigger**: Sandbox token renewal (deprecated — only production tokens are used for data and trading)  
**Purpose**: Informational only. Sandbox tokens are deprecated; the app uses **production tokens only** for data and API. You only need to renew the production token.
**Delivery**: Direct Telegram API call (works 24/7, independent of trading system)

---

## ⚙️ **Alert Configuration**

### **Configuration Files**

**Local Development**: `secretsprivate/telegram.env`
```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
TELEGRAM_ENABLED=true
```

**Production**: Google Cloud Secret Manager
- `telegram-bot-token`
- `telegram-chat-id`

**Alert Settings**: `configs/Alerts.env`
```bash
# Enable Telegram alerts
TELEGRAM_ALERTS_ENABLED=true

# Rate limiting
TELEGRAM_MAX_MESSAGES_PER_MINUTE=20
TELEGRAM_RATE_LIMIT_ENABLED=true
TELEGRAM_ALERT_COOLDOWN_SECONDS=60

# Alert types
TELEGRAM_ALERT_TYPES=entry,exit,error,performance,daily_summary,system_status
```

### **Alert Manager**

The alert manager (`modules/prime_alert_manager.py`) handles:
- Alert formatting
- Telegram delivery
- Error handling
- Rate limiting
- Alert deduplication
- Trade ID generation

**Multi-ledger string constants** (for consistent Telegram copy): `ALERT_LABEL_EASY_ORB_ETF`, `ALERT_LABEL_EASY_ORB_0DTE_OPTIONS`, `ALERT_LABEL_EASY_TRENDLINE_0DTE` — used in HTML bodies and Trendline/system titles.

---

## 📅 **Daily Alert Flow**

**Typical Trading Day** (Monday-Friday):

1. **9:00 PM PT (Midnight ET)**: OAuth Tokens Expired alert 🔴 (if tokens expired)
2. **5:30 AM PT (8:30 AM ET)**: Good Morning alert 🌅
   - Time validation: Only sends 5:30-5:35 AM PT
   - Deduplication: One alert per day maximum
3. **6:45 AM PT**: ORB Capture Complete (all symbols captured)
4. **7:30 AM PT - Step 1**: Trade Signal Collection (SO + 0DTE in one alert; shows 6-15 signals or 0 signals; when 0 signals, may include diagnostic reason)
   - Deduplication: GCS-based
5. **7:30 AM PT - Step 2**: SO Execution (shows executed trades with bold formatting)
6. **7:30 AM PT - Step 3**: 0DTE Options Execution (if enabled, shows options trades)
7. **Every 15 Min (trading loop)**: **Easy ORB (ETF)** portfolio health 🛡️ (`BAD DAY` / `WEAK DAY` Telegram; batch closes in demo)
8. **Throughout Day**: ETF stealth exits, rapid exits, aggregated batch exits as applicable
9. **Throughout Day (if 0DTE enabled)**: **Easy ORB 0DTE** position monitoring → `send_options_position_exit_alert`, partials, runners, aggregated closes; demo **MockOptionsExecutor** may also send **system** alerts on each open/close
10. **Throughout Day (if Trendline enabled)**: **Easy Trendline 0DTE** monitor + exits + optional throttled monitor alert
11. **~12:55 PM PT** (**`SO_ETF_EOD_CLOSE_*`**, default **12:55**–**12:56** PT): **`flatten_all_paths_for_eod_scheduler()`** — ORB ETF + Easy ORB 0DTE + Trendline 0DTE (requires main-loop **`orb_strategy_manager`** + **`stealth_trailing`**; otherwise rely on step 12 flatten inside HTTP handler)
12. **~1:05 PM PT (4:05 PM ET)** (Cloud Scheduler): **`POST /api/end-of-day-report`** — optional deduped flatten then **three** Telegram EOD summaries when components exist: **Easy ORB (ETF)**, **Easy ORB 0DTE**, **Easy Trendline 0DTE** (Rev 00260)

**Holidays/Weekends**:
- **Holiday Alert**: Sent at 5:30 AM PT instead of Good Morning alert
- **No Trading Alerts**: System skips trading-related alerts on holidays

---

## 🎨 **Alert Formatting**

### **Execution Alerts**

**Enhanced Formatting**:
- **Bold Priority Rank**: `<b>Rank #1</b>`
- **Bold Priority Score**: `<b>Priority Score: 0.856</b>`
- **Bold Confidence**: `<b>Confidence: 85%</b>`
- **Bold Momentum**: `<b>Momentum: 75/100</b>`
- **Bold Delta**: `<b>Delta: 0.25</b>`

**Trade ID Format**:
- **Shortened Format**: `DEMO_QQQ_260105_485_488_c_704400`
- **Components**:
  - Mode: `DEMO` or `LIVE`
  - Symbol: `QQQ`, `SPY`, `SPX`, `IWM`, etc.
  - Date: `260105` (YYMMDD format)
  - Strike/Price info: `485_488` (spread) or `628` (single-leg)
  - Strategy type: `c` (call), `p` (put), `d` (debit), `cr` (credit), `l` (lotto)
  - Unique ID: `704400`
- **Applied To**: All strategies (ORB and 0DTE), both Demo and Live modes

### **Exit Alerts**

- Clear exit reason
- Entry/exit prices
- P&L highlighted
- Hold time displayed
- Peak price reached
- Trade ID (shortened format)

### **Error Alerts**

- Error type and message
- Affected symbols or positions
- Recovery actions taken
- Next steps

---

## 🛠️ **Troubleshooting**

### **Common Issues**

**1. Alerts Not Received**
- ✅ Check Telegram bot token and chat ID configuration
- ✅ Verify Cloud Scheduler jobs are running
- ✅ Check Cloud Run service logs for errors
- ✅ Test bot manually: `https://api.telegram.org/bot<TOKEN>/getMe`
- ✅ Verify bot is not blocked

**2. Duplicate Alerts**
- ✅ Should be fixed with deduplication (GCS-based)
- ✅ Check alert deduplication logic
- ✅ Verify Cloud Scheduler jobs aren't running multiple times

**3. Trade IDs Too Long**
- ✅ Should be fixed with shortened format (Rev 00231)
- ✅ Verify shortened format is being used
- ✅ Format: `DEMO_QQQ_260105_485_488_c_704400`

**4. Missing Bold Formatting**
- ✅ Should be fixed with enhanced formatting (Rev 00231)
- ✅ Check alert manager formatting code
- ✅ Verify HTML formatting is enabled

**5. Too Many Alerts (Spam)**
- ✅ Adjust `TELEGRAM_MAX_MESSAGES_PER_MINUTE` in config
- ✅ Enable `TELEGRAM_RATE_LIMIT_ENABLED`
- ✅ Increase `TELEGRAM_ALERT_COOLDOWN_SECONDS`

**6. Alerts Received But Formatting Broken**
- ✅ Check that HTML formatting is enabled (default)
- ✅ Verify message doesn't contain invalid HTML
- ✅ System will auto-fallback to plain text if HTML fails

**7. Many 0DTE names in Signal Collection, few or zero in Options Execution (Rev 00318)**
- ✅ Expected when Convex, Hard Gate, position caps, or chain/momentum gates drop symbols after the scan
- ✅ Read **0DTE pipeline (this run)** in the Options Execution alert (Watchlist → Collection underlyings → Convex-qualified → Hard gate queue → Execution attempts)
- ✅ Optional Telegram note explains when collection underlyings exceed execution attempts; grep logs for `CONVEX_REJECT`, `HARD_GATE_SUMMARY`, and rejection reasons in the zero-trade alert

### **Useful Commands**

```bash
# Test Telegram bot
curl "https://api.telegram.org/bot<TOKEN>/getMe"

# Test sending message
curl -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  -d "chat_id=<CHAT_ID>" \
  -d "text=Test message"

# View recent logs
gcloud run services logs read easy-etrade-strategy --region=us-central1 --limit 50
```

---

## 📝 **Revision History**

### **Latest Updates (May 11, 2026 - Rev 00332)**

**Rev 00332 (May 11 - ORB 0DTE two-stage durability + quote_grade in execution alert)**:
- ✅ Options Execution HTML: **`execution_durable`** (Stage A), **`exit_grade_durable`** (Stage B), **`monitored_but_degraded`**
- ✅ Durable follow-up rows: **`quote_grade`**, **`exit_grade_ready`**; status copy when Stage A is satisfied but exit-grade marks are pending
- ✅ Ops grep extended for lifecycle / overextension / chain fallback / spread degraded-exit telemetry (see **Ops / logs** under Easy ORB 0DTE Options Execution)

### **Latest Updates (May 7, 2026 - Rev 00331)**

**Rev 00331 (May 7 - ORB 0DTE durability bucket alert refresh)**:
- ✅ ORB 0DTE Options Execution alert classifies orders into canonical buckets: `durable_executed`, `submitted_pending_confirmation`, `monitoring_active_not_yet_durable`, `chain_health_failed`, `selector_failed`, `durability_failed`, `broker_submit_failed`
- ✅ Pipeline section renames stale labels to `Execution attempts (chain + momentum + strategy handling)`, `Submitted total (broker accepted)`, `Pending confirmation (not durable yet)`, `Dropped before Hard Gate (Convex + dedupe + top-N cap)`, `True failed/rejected (execution step)`
- ✅ When orders submitted but not yet durable, alert renders `⏳ ORB 0DTE orders submitted; awaiting durable confirmation` (with a per-symbol pending block) instead of `NO ... TRADES EXECUTED`
- ✅ `🧪 Chain health diagnostics` block documented (retry / relaxed pass / extension rejects / per-failure-type histogram)
- ✅ New telemetry markers: `ORB_0DTE_DURABILITY_ALERT_BUCKETS`, `ORB_0DTE_DURABILITY_RECONCILE_START`, `ORB_0DTE_DURABILITY_RECONCILE_RESULT`
- ✅ Monitoring line in execution alert example aligned to actual code string `All positions tracked (fast loop ~7s + shared backup)`

**Rev 00330 (Apr 29 - Trendline close/exec format + dynamic watchlist count docs)**:
- ✅ Trendline execution title docs updated to `Trendline Options Execution`
- ✅ Trendline close title/docs updated to `TRENDLINE OPTION CLOSED` with enriched close payload fields
- ✅ `0dte_list.csv` watchlist examples switched from fixed number to dynamic count

**Rev 00329 (Apr 17 - Tier-1 Red Day + selector diagnostics)**:
- ✅ Red Day alert content updated to Tier-1-aware wording (non-Tier-1 CALL/LONG blocked, Tier-1 CALL/LONG continues, PUT/SHORT continues)
- ✅ 0DTE execution diagnostics expanded for ITM spread selector with leg-level stages (`long_leg_unavailable`, `short_leg_unavailable`, `long_leg_liquidity`, `short_leg_liquidity`)
- ✅ Ops grep hints updated to include `0DTE_SELECTOR_DIAG` and `ITM_SPREAD_SELECTOR`
- ✅ Watchlist examples aligned to current `0dte_list.csv` size **82**

### **Latest Updates (April 13, 2026 - Rev 00327)**

**Rev 00327 (Apr 13 - Watchlist / doc parity)**:
- ✅ **0dte_list.csv** size **82** in examples; merged ORB capture **~205** symbols in ORB Capture / Red Day examples
- ✅ Cross-reference **docs/Data.md** for Tier **9** / **71** and underlying-vs-2× policy

### **Latest Updates (April 7, 2026 - Rev 00319)**

**Rev 00319 (Apr 7 - Multi-ledger alerts + doc truth)**:
- ✅ Document **Easy ORB (ETF)**, **Easy ORB 0DTE**, **Easy Trendline 0DTE** headers and **three** Cloud Scheduler EOD reports
- ✅ ETF 15m health: actual **`send_telegram_alert`** titles (**Easy ORB (ETF) — …**); not generic “Portfolio Health: EMERGENCY”
- ✅ **Easy ORB 0DTE** execution/exit/EOD examples aligned with `prime_alert_manager` + **MockOptionsExecutor** system alerts
- ✅ New section **Easy Trendline 0DTE Alerts** (dedicated Trendline fill/exit senders + optional monitor heartbeat path)
- ✅ **send_options_health_check_alert**: documented as **not called** from main loop today

### **Latest Updates (March 25, 2026 - Rev 00318)**

**Rev 00318 (Mar 25 - 0DTE execution alert pipeline + ops hooks)**:
- ✅ **0DTE Options Execution** documents **0DTE pipeline (this run)** (Watchlist, collection underlyings, Convex-qualified, Hard gate queue, execution attempts, drops/rejects)
- ✅ Zero-trade path: grouped rejection reasons, per-symbol detail cap, optional “fewer names than Signal Collection” note
- ✅ Ops: grep **`SO_PIPELINE`**, **`0DTE_DEMO | synthetic_chain`**

### **Latest Updates (February 26, 2026 - Rev 00292)**

**Rev 00292 (Feb 26 - Convex filter 0-pass diagnosis)**:
- ✅ When 0DTE Convex filter rejects all signals: check-by-check failure counts (Volatility, ORB Range/ATR, Red Day, ORB Break, Volume, VWAP, Momentum, Market Regime, Score)
- ✅ Grep-friendly one-liner: `CONVEX_FILTER | 0_eligible | total=N | top_failures: ...`
- ✅ Top 5 per-symbol rejection details at INFO
- ✅ Input LONG/SHORT counts; eligible CALL/PUT breakdown; direction on logs

**Rev 00289 (Feb 26 - CRITICAL: Signal append bug)**:
- ✅ Fixed: Signal creation and append now run inside `if orb_result.should_trade` — symbols that passed all 3 rules now correctly appear in Signal Collection lists (ORB and 0DTE).

### **Previous Updates (February 9, 2026 - Rev 00259)**

**Rev 00259 (Feb 9 - Alerts doc alignment)**:
- ✅ Trade Signal Collection: clarified single alert for SO + 0DTE; 0-signals diagnostic reason documented
- ✅ 0DTE ORB Capture: clarified integrated into ORB Capture Complete (counts only; no separate alert)
- ✅ 0DTE Signal Collection: clarified no separate alert; included in Trade Signal Collection
- ✅ Revision history and footer date aligned

**Rev 00260 (EOD Single Source Consolidation)**:
- ✅ EOD reports consolidated to single source (Cloud Scheduler endpoint only)
- ✅ GCS-based deduplication active for both ORB and 0DTE EOD reports
- ✅ Timing: 1:05 PM PT (4:05 PM ET)

### **Previous Updates**

**Rev 00246 (Jan 19 - 0DTE Strategy Improvements)**:
- ✅ 0DTE Priority Score Formula v1.1
- ✅ Direction-Aware Red Day Filtering
- ✅ Expanded Delta Selection (0.15-0.35)

**Rev 00233 (Jan 8 - Performance Improvements & Data Quality Fixes)**:
- ✅ Secrets Management: All sensitive credentials moved to `secretsprivate/`
- ✅ Good Morning Alert Time Validation: Only sends 5:30-5:35 AM PT
- ✅ Good Morning Alert Deduplication: GCS-based (one alert per day maximum)

**Rev 00231 (Jan 6 - Trade ID Shortening & Alert Formatting)**:
- ✅ Trade ID Shortening: Shortened trade IDs for cleaner format
- ✅ Alert Formatting Enhancements: Bold formatting for key metrics

---

**Alert System User Guide - Complete and Ready for Use!** 🚀

*Last Updated: May 14, 2026*  
*Version: Rev 00332 (May 11 — two-stage durability counters + quote_grade / exit_grade_ready in ORB 0DTE execution alert; related lifecycle and spread-relief log tokens); **May 14** — **`ORB_0DTE_SELECTOR_FULL_REPLAY`**, **`SO_CONTINUATION_VS_EXTENSION_BIAS`** / **`SO_RANK_BREAKDOWN`** grep; Rev 00331 (ORB 0DTE durability bucket alert refresh — canonical buckets, awaiting-durable path, chain health diagnostics block, durability reconcile telemetry); Rev 00330 (Trendline close/exec format refresh + dynamic `0dte_list.csv` count docs); Rev 00329 (Tier-1 Red Day behavior + ITM selector leg diagnostics + new log tokens); Rev 00328 (spread_selection_failed / “No debit spread found” in 0DTE execution text); Rev 00321 EOD trio format; Rev 00318 pipeline block; Rev 00292 Convex diagnosis; Rev 00260 EOD single source*  
*Maintainer: Easy ORB Strategy Development Team*
