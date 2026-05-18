# Easy 0DTE Strategy - Detailed Strategy Documentation

**Last Updated**: April 14, 2026  
**Version**: **Rev 00349** (+ **2.38.0 / Rev 00238** baseline). ORB 0DTE fast-monitor + execution reliability tuning; Hard Gate vs execution liquidity; structured `0DTE_*` logging.  
**Status**: ✅ **PRODUCTION ACTIVE**

---

## 📋 **Table of Contents**

1. [Strategy Overview](#strategy-overview)
2. [Daily Trading Workflow](#daily-trading-workflow)
3. [Options Trading Strategies](#options-trading-strategies)
4. [Entry Rules & Criteria](#entry-rules--criteria)
5. [Convex Eligibility Filter](#convex-eligibility-filter)
6. [Signal Generation](#signal-generation)
7. [Options Chain Management](#options-chain-management)
8. [Trade Execution](#trade-execution)
9. [Exit Framework](#exit-framework)
10. [Risk Management](#risk-management)
11. [Profit Management](#profit-management)
12. [Position Sizing](#position-sizing)
13. [Priority Ranking System](#priority-ranking-system)

---

## 🎯 **Strategy Overview**

### **Core Concept**

The Easy 0DTE Strategy provides **selective convex amplification** of high-conviction ORB signals through 0DTE (Zero Days To Expiration) options. Unlike traditional leverage, 0DTE options offer:

- **Gamma Exposure**: Rapid price appreciation on directional moves
- **Time Decay**: Expires same day (no overnight risk)
- **Capital Efficiency**: Lower capital requirement than buying shares
- **Defined Risk**: Spreads limit maximum loss

### **Philosophy**

**"Not every ORB-qualified trade gets options—only the highest-conviction setups."**

The strategy uses a **Convex Eligibility Filter** to identify only the most promising ORB signals for options exposure, ensuring:
- High volatility (top 20% percentile)
- Significant ORB range (≥0.35%) or sufficient ATR
- Strong momentum confirmation
- Trend/impulse market regime (not rotation)
- Red Day handling is **direction-aware** at execution: **Long/CALL** blocked on Red Days; **Short/PUT** can remain eligible (Rev 00246+)

---

## ⏰ **Daily Trading Workflow**

### **Complete Trading Day Timeline**

The 0DTE Strategy operates in conjunction with the ORB Strategy throughout the trading day:

#### **6:30-6:45 AM PT: ORB Capture Window** ⭐ **SHARED WITH ORB STRATEGY**

**Purpose**: Establish opening range for all symbols (ORB + 0DTE) - **shared for both strategies**

**Process**:
1. Load symbols from `core_list.csv` (ORB symbols — **129** data rows)
2. Load symbols from `0dte_list.csv` (0DTE symbols — **80** data rows)
3. Merge lists (add 0DTE symbols not already in ORB list)
4. Batch capture ORB data for all symbols using E*TRADE batch quotes
5. Store ORB High/Low for each symbol

**Data Source**: E*TRADE batch quotes (25 symbols per call)

**Duration**: 2-5 seconds for all symbols (batch processing)

**Alert**: **Single ORB Capture Alert** sent at 6:45 AM PT with ORB data for **both SO trades and 0DTE trades**

**0DTE Integration**: All 0DTE symbols included in ORB capture (shared with ORB Strategy)

#### **7:00-7:15 AM PT: Pre-Fetch Window**

**Purpose**: Pre-fetch 7:00-7:15 AM candle data for instant signal validation

**Process**:
1. Fetch 15-minute candle data for all symbols
2. Calculate volume colors and technical indicators
3. Store in memory for instant access during signal collection

**Data Source**: E*TRADE batch quotes

**Duration**: 2-3 seconds

**0DTE Integration**: 0DTE symbols included in pre-fetch

#### **7:15-7:30 AM PT: Signal Collection & Rules Confirmation Window** ⭐ **FINAL CONFIRMED LISTS**

**Purpose**: Collect ORB signals and 0DTE signals, confirm all rules and risk management, generate final confirmed trade lists ready for execution

**Process**:

**1. ORB Strategy - SO Signal Collection:**
   - Scan all symbols for ORB breakouts/breakdowns
   - Use pre-fetched 7:00-7:15 AM data for instant validation
   - **Rules Confirmation** (after ORB Capture):
     - 3 strict validation rules (price, volume color, previous candle)
     - Red Day Filter (Portfolio-Level)
     - Signal-Level Filtering
   - **Risk Management**:
     - Position sizing (rank-based multipliers)
     - Capital allocation (90% allocation)
     - Position limits (max 15 concurrent)
   - **Final SO Signal Collection**: Final confirmed SO trades ready for execution (after all rules and risk management)

**2. 0DTE Strategy - Options Signal Collection (Long and Short):**
   - **0DTE produces both LONG (CALL) and SHORT (PUT) signals** — unlike ORB SO, which is Long-only.
   - Receive ORB breakout (LONG) and breakdown (SHORT) signals from the scan; build a single **0DTE Signal Collection list** containing both directions.
   - **Rules Confirmation** (after ORB Capture):
     - Convex Eligibility Filter (score ≥ 0.75, 8 criteria) — applied to both LONG and SHORT (direction-aware: e.g. Red Day rejects LONG, allows SHORT).
     - Strategy selection (long call / long put, debit spread, momentum scalper, ITM probability)
     - **Hard Gate** (pre-queue): eligible symbol/target allowlist, session **time window**, **volume / volume_ratio**; **very wide ORB %** → **warning only** (no Hard Gate reject on max ORB range — Rev 00326).
     - **Strike selection at execution**: delta, premium, and **options-chain** liquidity (e.g. OI, spread %, volume — `0DTE_OPTIONS_*` env)
     - Position size validation (capital allocation, max position limits)
     - Red Day check (portfolio-level, direction-aware)
   - **Risk Management**:
     - Position limits (max 15 concurrent positions, **combined** CALL + PUT)
     - Capital allocation (Tier 1: 35%, Tier 2: 20%, Tier 3: 10%)
     - Liquidity requirements (bid/ask spread, open interest, volume)
   - **Final 0DTE Signal Collection**: Single list of confirmed LONG and SHORT options trades; ranked together by priority; top N (up to 15) executed as CALL and PUT options.

**3. Signal Collection Alert (7:30 AM PT)**:
   - **Single alert** showing both final confirmed trade lists:
     - **SO Signal Collection**: Final confirmed SO trades (after all rules and risk management) - ready for execution
     - **0DTE Signal Collection**: Final confirmed 0DTE options trades (after all rules and risk management) - ready for execution
   - Both lists represent **final execution-ready trades** confirmed to open positions (after all rules and risk management)
   - Includes signal counts, qualified signals, and symbol lists
   - Sent at 7:30 AM PT (or within grace period 7:30-7:40 AM PT)

**0DTE Signals Generated** (Final Confirmed List — **both Long and Short**):
- **LONG (CALL)**: Long calls (momentum ≥ 80, ORB range ≥ 0.40%, delta 0.15, premium $0.15-$0.60); debit spreads; momentum scalpers; ITM probability spreads.
- **SHORT (PUT)**: Put debit spreads and long puts (same momentum/range logic, direction-aware Convex and Red Day).
- The **combined Long + Short list** is ranked by priority (one ranking for all 0DTE signals); top up to 15 are executed as options (CALLs and PUTs).

**Real Market Data** (Rev 00237):
- SPY momentum: Retrieved from E*TRADE (real-time)
- VIX level: Retrieved from E*TRADE (real-time)
- Used for Enhanced Red Day Detection

**Key Points**:
- Both strategies confirm rules **after ORB Capture** and **before execution**
- Signal Collection alert contains **final confirmed trade lists** ready for execution (after all rules and risk management)
- All rules and risk management applied during Signal Collection window (7:15-7:30 AM PT)

#### **7:30 AM PT: Trade Execution Window** ⭐ **SEPARATE EXECUTION ALERTS**

**Purpose**: Execute trades from final confirmed lists (SO Signal Collection and 0DTE Signal Collection)

**Process**:
1. **ORB SO Execution**:
   - Execute trades from **SO Signal Collection** (final confirmed list)
   - All rules and risk management already applied during Signal Collection
   - Position sizing, capital allocation, and rank multipliers already calculated
   - Execute ORB ETF trades (SO signals)
   - Send **Separate ORB SO Execution Alert** (after execution)

2. **0DTE Options Execution**:
   - Execute trades from **0DTE Signal Collection** (final confirmed list)
   - All rules and risk management already applied during Signal Collection:
     - Strategy type already selected (long call, debit spread, etc.)
     - **Hard Gate** already passed (allowlist, time window, volume checks)
     - Position sizing already calculated (capital allocation, max limits)
   - At execution time: options chain fetched; **strikes** chosen/validated with **chain liquidity** guardrails (OI, spread, volume)
   - For each confirmed 0DTE signal:
     - Fetch options chain from E*TRADE API (for execution)
     - Select strikes against live chain data (delta, premium, **liquidity**), then execute (Demo/Live mode)
   - Send **Separate 0DTE Options Execution Alert** (after execution)

**Execution Alerts**:
- **ORB SO Execution Alert**: Shows executed ORB ETF trades from SO Signal Collection
- **0DTE Options Execution Alert**: Shows executed options trades from 0DTE Signal Collection
- Both alerts sent **after** trades are executed
- **Note**: Signal Collection alert (sent before execution) contains final confirmed trade lists ready for execution

**Entry Rules Applied** (During Signal Collection - 7:15-7:30 AM PT):
- Strategy type selection (long call, debit spread, momentum scalper, ITM probability)
- **Hard Gate** (pre-queue): allowlist, time window, volume / volume_ratio; wide ORB → warn only (Rev 00326)
- Position sizing (capital allocation, max position limits)
- **Chain liquidity** (spread, OI, volume) applied when selecting contracts **at execution** against live chain data
- All rules and risk management confirmed before / during execution as appropriate

#### **7:30 AM - 12:55 PM PT: Position Monitoring**

**Purpose**: Monitor all open positions and execute exits

**Process** (fast loop every ~5 seconds, with shared backup loop):
1. **Real-Time Options Price Updates** (Rev 00238):
   - Fetch current options quotes from E*TRADE API
   - Update `position.current_value` with real options prices
   - Calculate unrealized P&L based on actual options moves

2. **Exit Condition Checks**:
   - Profit targets (+60%, +120%)
   - Hard stops (-45% for debit spreads, -55% for lotto)
   - Invalidation stops (VWAP/ORB reclaim)
   - Time stops (25 min for debit spreads, 12 min for lotto)
   - Fail-safe stops (-60% absolute)

3. **Exit Execution**:
   - Execute partial profits (sell 50% at +60%, 25% at +120%)
   - Execute full exits (hard stops, invalidation, time stops)
   - Send Position Exit Alerts

**Monitoring Frequency**:
- ORB 0DTE: ~5 seconds (primary fast loop), plus shared backup loop for resilience
- Trendline 0DTE: ~5 seconds (fast monitor)

**Price Tracking** (Rev 00238):
- **Before**: Exit decisions based on underlying price movement
- **After**: Exit decisions based on actual options prices
- **Example**: QQQ moves +0.86% → Option moves +410% ($0.19 → $0.97)
- **Impact**: Accurate profit target triggers and exit decisions

#### **12:55 PM PT: End of Day Close**

**Purpose**: Close all open positions before market close

**Process**:
1. Identify all open options positions
2. Close all positions via E*TRADE API (Live) or Mock Executor (Demo)
3. Send aggregated EOD Close Alert
4. Calculate final P&L for the day

**Alert**: Options EOD Close Alert sent with final statistics

**Timing**: 5 minutes before market close (3:00 PM ET / 12:00 PM PT)

---

## 🎯 **Options Trading Strategies**

### **Strategy Selection Matrix**

The 0DTE Strategy selects options strategies based on momentum score and market conditions:

| Momentum Score | ORB Range | Strategy Type | Strike Selection | Premium Target | Use Case |
|---------------|-----------|--------------|------------------|----------------|----------|
| ≥ 90 | ≥ 0.50% | **Lotto** | OTM (delta 0.15) | $0.15-$0.60 | Extreme momentum, trend day |
| ≥ 80 | ≥ 0.40% | **Long Call/Put** | OTM (delta 0.15) | $0.15-$0.60 | High momentum, volatility expansion |
| ≥ 70 | ≥ 0.35% | **Momentum Scalper** | ATM (1-2 strikes OTM) | $0.20-$0.60 | Quick expansion expected |
| 45-70 | ≥ 0.25% | **ITM Probability** | ITM (delta 0.65) | Higher premium | Stable conditions, higher probability |
| 55-80 | Default | **Debit Spread** | OTM (delta 0.15-0.35) | $0.15-$0.60 | Standard strategy (most common) |
| < 45 | Any | **No Trade** | N/A | N/A | Low momentum or chop detected |

### **Strategy 1: Long Calls/Puts** (Rev 00238 - Optimized)

**Trigger Conditions**:
- Momentum Score ≥ 80
- ORB Range ≥ 0.40%
- Volatility expanding

**Strike Selection**:
- **Target Delta**: 0.15 (cheap OTM options for maximum gamma explosion)
- **Premium Range**: $0.15-$0.60 (allows $0.19 entries like successful trades)
- **Strike Position**: 1-3 strikes OTM (out of the money)
- **Purpose**: Maximum gamma exposure for explosive moves

**Entry Rules**:
1. Signal must pass Convex Eligibility Filter (score ≥ 0.75)
2. Momentum score ≥ 80 (high conviction)
3. ORB range ≥ 0.40% (volatility expansion)
4. Options chain fetched from E*TRADE API
5. Strike selected: Delta 0.15, Premium $0.15-$0.60, 1-3 strikes OTM
6. Liquidity validation: Bid/ask spread ≤ 5%, Open interest ≥ 100
7. Position size: 40% of allocated capital (0.4x normal risk)

**Example**:
- **QQQ Signal**: Momentum 85, ORB range 0.45%
- **Selected**: QQQ 628c @ $0.19 (delta 0.12, ~1% OTM)
- **Result**: If QQQ moves +0.86%, option moves +410% ($0.19 → $0.97)

**Capital Allocation**: 40% of allocated capital (higher than lotto due to better risk/reward)

### **Strategy 2: Debit Spreads** (Primary Strategy - Most Common)

**Trigger Conditions**:
- Momentum Score 55-80 (default)
- ORB Range ≥ 0.35% (standard threshold)
- Any volatility level

**Strike Selection**:
- **Target Delta**: 0.15-0.25 (based on volatility)
  - High volatility (≥0.50%): 0.25 delta
  - Moderate volatility (≥0.35%): 0.20 delta
  - Lower volatility (<0.35%): 0.15 delta
- **Premium Range**: $0.15-$0.60 per leg
- **Spread Width**:
  - SPX: $5-$10
  - QQQ/SPY: $1-$2
  - Other: $1-$2
- **Strike Position**: 1-3 strikes OTM

**Entry Rules**:
1. Signal must pass Convex Eligibility Filter (score ≥ 0.75)
2. Momentum score ≥ 55 (minimum threshold)
3. Options chain fetched from E*TRADE API
4. Long leg selected: Target delta ± 0.05, Premium $0.15-$0.60
5. Short leg selected: Long leg strike + spread width
6. Liquidity validation: Both legs must meet requirements
7. Position size: Full allocated capital (100% normal risk)

**Structure**:
- **Call Debit Spread** (LONG signals): Buy lower strike call, sell higher strike call
- **Put Debit Spread** (SHORT signals): Buy higher strike put, sell lower strike put
- **Max Profit**: Spread width - debit cost
- **Max Loss**: Debit cost (limited risk)

**Example**:
- **QQQ Signal**: Momentum 65, ORB range 0.38%
- **Selected**: QQQ 628/629 Call Debit Spread
  - Long leg: 628c @ $0.19 (delta 0.20)
  - Short leg: 629c @ $0.12 (delta 0.15)
  - Debit cost: $0.07 per spread
  - Max profit: $0.93 per spread (if both legs expire ITM)

### **Strategy 3: Momentum Scalper** (Quick Expansion)

**Trigger Conditions**:
- Momentum Score ≥ 70
- ORB Range ≥ 0.35%
- Quick expansion expected

**Strike Selection**:
- **Target Delta**: ATM or 1-2 strikes OTM
- **Premium Range**: $0.20-$0.60
- **Spread Width**: $1-$2 (narrow for quick payoff)

**Entry Rules**:
1. Signal must pass Convex Eligibility Filter (score ≥ 0.75)
2. Momentum score ≥ 70 (strong momentum)
3. Options chain fetched from E*TRADE API
4. ATM or near-ATM strikes selected (1-2 strikes OTM)
5. Narrow spread width for quick payoff
6. Position size: Full allocated capital

**Purpose**: Fast payoff on quick directional moves

### **Strategy 4: ITM Probability Spread** (Higher Probability)

**Trigger Conditions**:
- Momentum Score 45-70
- ORB Range ≥ 0.25%
- Stable conditions

**Strike Selection**:
- **Target Delta**: 0.65 (deeper ITM for higher probability)
- **Premium Range**: Higher premium (ITM options)
- **Spread Width**: Standard ($1-$2)

**Entry Rules**:
1. Signal must pass Convex Eligibility Filter (score ≥ 0.75)
2. Momentum score 45-70 (moderate momentum)
3. Options chain fetched from E*TRADE API
4. ITM strikes selected (delta 0.60-0.70)
5. Higher probability, lower breakeven
6. Position size: Full allocated capital

**Purpose**: Higher probability trades with lower breakeven

### **Strategy 5: Lotto Sleeve**

**Trigger Conditions**:
- Momentum Score ≥ 90
- ORB Range ≥ 0.50%
- Trend day confirmed
- Lotto sleeve enabled when directional-strength adjusted momentum qualifies

**Strike Selection**:
- **Target Delta**: 0.15 (very cheap OTM)
- **Premium Range**: $0.15-$0.60
- **Max Cost**: $200 per position

**Entry Rules**:
1. Signal must pass Convex Eligibility Filter (score ≥ 0.75)
2. Momentum score ≥ 90 (extreme momentum)
3. Directional-strength adjusted momentum must meet lotto threshold
4. Options chain fetched from E*TRADE API
5. Very cheap OTM strikes selected
6. Position size: 35% of allocated capital (0.35x normal risk)

**Purpose**: Maximum leverage for extreme moves (rare, high risk)

---

## 📋 **Entry Rules & Criteria**

### **Pre-Entry Validation**

All 0DTE signals must pass multiple validation layers before execution:

#### **1. Convex Eligibility Filter** (Required)

**Minimum Score**: 0.75 (75%)

**Components**:
- Volatility Score (40% weight): ≥ 80th percentile
- ORB Range/ATR (25% weight): ≥ 0.35% OR ATR ≥ 0.25%
- NOT Red Day (15% weight): Must pass Red Day check
- Momentum (10% weight): Positive MACD, RS vs SPY, or VWAP distance
- Trend Day (10% weight): Trend/impulse regime (not rotation)

**Rejection**: Signal rejected if score < 0.75

#### **2. Hard Gate Validation** (Required)

**Liquidity Requirements**:
- **Minimum Open Interest**: 100 contracts
- **Maximum Bid-Ask Spread**: 5% of mid price
- **Minimum Volume**: 50 contracts (intraday)

**Validation Process**:
1. Fetch options chain from E*TRADE API
2. Check long leg (and short leg for spreads) meet requirements
3. Reject if any leg fails liquidity check

**Rejection**: Signal rejected if liquidity requirements not met

#### **3. Strategy Type Selection** (Required)

**Selection Logic**:
- Momentum score ≥ 80 AND ORB range ≥ 0.40% → Long Call/Put
- Momentum score ≥ 70 AND ORB range ≥ 0.35% → Momentum Scalper
- Momentum score 45-70 AND ORB range ≥ 0.25% → ITM Probability Spread
- Momentum score 55-80 (default) → Debit Spread
- Momentum score < 45 OR chop detected → No Trade

**Rejection**: Signal rejected if strategy type = 'no_trade'

#### **4. Strike Selection** (Required)

**Target Delta**:
- Long calls: 0.15 (cheap OTM for gamma explosion)
- Debit spreads: 0.15-0.25 (based on volatility)
- Momentum scalpers: ATM or 1-2 strikes OTM
- ITM probability: 0.65 (deeper ITM)

**Premium Range**: $0.15-$0.60 (Rev 00238: lowered from $0.20)

**Strike Position**: 1-3 strikes OTM (for OTM strategies)

**Validation**:
- Strike must exist in options chain
- Premium must be within range
- Delta must be within target range (± 0.05)

**Rejection**: Signal rejected if no valid strike found

#### **5. Position Size Validation** (Required)

**Max Position Size**: ≤ 35% of account equity

**Capital Allocation**:
- Tier 1 (Top 3): 35% of allocated capital each
- Tier 2 (Next 5): 20% of allocated capital each
- Tier 3 (Remaining): 10% of allocated capital each

**Validation**:
- Calculate position cost (premium × quantity × 100)
- Check position cost ≤ allocated capital
- Check total deployed capital ≤ available capital

**Rejection**: Signal rejected if position size exceeds limits

#### **6. Red Day Check** (Required)

**Portfolio-Level Protection**:
- Enhanced Red Day Detection uses real SPY momentum and VIX level (Rev 00237)
- If Red Day detected: All trades blocked (portfolio-level)
- If Red Day not detected: Individual signals evaluated

**Rejection**: All trades rejected if Red Day detected

### **Entry Execution Rules**

Once all validations pass, entry execution follows:

1. **Options Chain Fetch**:
   - Fetch current options chain from E*TRADE API
   - Include Greeks (delta, gamma, theta, vega, IV)
   - Use today's expiry (0DTE)

2. **Strike Selection**:
   - Select strikes based on strategy type and target delta
   - Validate premium range ($0.15-$0.60)
   - Validate liquidity (bid/ask spread, open interest)

3. **Position Size Calculation**:
   - Calculate quantity based on allocated capital
   - Quantity = Allocated Capital / (Premium per contract × 100)
   - Minimum quantity: 1 contract

4. **Order Placement**:
   - **Demo Mode**: Simulate order execution
   - **Live Mode**: Place order via E*TRADE API
   - Order type: MARKET (for immediate execution)

5. **Position Creation**:
   - Create `OptionsPosition` object
   - Track entry price, entry time, quantity, strategy type
   - Initialize monitoring flags

6. **Alert Notification**:
   - Send Options Execution Alert
   - Include position details, capital deployed, strategy type

### **Entry Rejection Reasons**

Signals may be rejected for:
- Convex Eligibility Filter score < 0.75
- **Hard Gate** failed (allowlist, time window, volume / volume_ratio — Rev 00326)
- **Execution / chain** rejection (liquidity, spread, no acceptable strike, payoff guardrails — Rev 00319+)
- Strategy type = 'no_trade' (low momentum or chop)
- No valid strike found (premium/delta out of range)
- Position size exceeds limits
- Red Day / direction gating (e.g. Long blocked)
- Options chain fetch failed
- Insufficient capital available

**Cloud logs (Rev 00326):** grep `0DTE_HARD_GATE_REJECT`, `0DTE_EXEC_REJECT`, `CONVEX_REJECT_DETAIL`, `0DTE_PIPELINE`, etc.

---

## 🔍 **Convex Eligibility Filter**

### **Filter Components**

The Convex Eligibility Filter evaluates ORB signals across **8 criteria**:

#### **1. Volatility Score (40% weight)**

- **Requirement**: Volatility score ≥ Top 20% percentile (80th percentile)
- **Leveraged ETFs**: Lower threshold (60th percentile)
- **Purpose**: Ensures sufficient volatility for gamma explosion

#### **2. ORB Range OR 5-min ATR (25% weight)**

- **Requirement**: 
  - ORB range ≥ 0.35% of symbol price **OR**
  - 5-minute ATR ≥ 0.25% of price (intraday minimum threshold)
- **Purpose**: Prevents theta chop in low-volatility environments

#### **3. Red Day / regime (15% weight)**

- **Requirement**: Convex uses a regime component; **execution** is **direction-aware** — Red Day blocks **Long/CALL**, **Short/PUT** can still qualify (Rev 00246+)
- **Purpose**: Reduce long risk on weak days without disabling bearish 0DTE opportunity

#### **4. ORB Break (Required)**

- **LONG Setup**: Price > ORB High (breakout confirmed)
- **SHORT Setup**: Price < ORB Low (breakdown confirmed)
- **Purpose**: Ensures actual breakout/breakdown occurred

#### **5. Volume Confirmation (Required)**

- **Requirement**: Current volume > ORB volume average
- **Purpose**: Confirms institutional participation

#### **6. VWAP Condition (Required)**

- **LONG Setup**: Price ≥ VWAP
- **SHORT Setup**: Price ≤ VWAP
- **Purpose**: Ensures alignment with intraday trend

#### **7. Momentum Confirmation (10% weight)**

- **Requirement**: Positive MACD histogram OR positive RS vs SPY OR positive VWAP distance
- **Purpose**: Confirms early momentum

#### **8. Market Regime (10% weight)**

- **Requirement**: Market regime = trend/impulse (NOT rotation)
- **Inference**: Strong VWAP distance (>1.0%) OR strong RS vs SPY (>2.0)
- **Purpose**: Ensures directional move (not choppy rotation)

### **Eligibility Score Calculation**

The filter calculates a **normalized eligibility score (0.0-1.0)**:

```
Score = (Volatility × 0.40) + (ORB_Range/ATR × 0.25) + (Not_Red_Day × 0.15) + 
        (Momentum × 0.10) + (Trend_Day × 0.10)
```

**Minimum Score**: 0.75 (75%) to qualify for options exposure

### **Rejection Reasons**

Signals that fail the filter receive detailed rejection reasons:
- Volatility score too low
- ORB range insufficient AND ATR insufficient
- Red Day / regime fails Convex check (direction still matters at execution for Long vs Short)
- No ORB break (price didn't break ORB High/Low)
- Volume below average
- VWAP condition not met
- Momentum confirmation missing
- Market regime is rotation (not trend)

---

## 📊 **Signal Generation**

### **0DTE Signal Collection: Long and Short** ⭐

- **ORB SO** Signal Collection produces **only Long** (equity/ETF) signals.
- **0DTE** Signal Collection produces **both Long (CALL) and Short (PUT)** signals from the same scan:
  - **LONG**: Price above ORB high (breakout) → CALL options (long call, call debit spread).
  - **SHORT**: Price below ORB low (breakdown) → PUT options (long put, put debit spread).
- The **combined list** (all LONG + SHORT that pass Convex and rules) is **ranked by priority** (single ranking); the **top N (max 15)** are used for position sizing and execution. Execution path supports both CALL and PUT trades.

### **Workflow**

1. **ORB Signal Reception**
   - Receives ORB signals from `PrimeORBStrategyManager` (both breakout and breakdown)
   - Signals include: symbol, **direction (LONG/SHORT)**, ORB data, technical indicators

2. **Convex Filtering**
   - Applies `ConvexEligibilityFilter` to all signals
   - Calculates eligibility scores
   - Filters to signals with score ≥ 0.75

3. **Symbol Mapping**
   - Maps ORB symbols to 0DTE symbols:
     - `TQQQ`, `QLD`, `SQQQ`, `QID` → `QQQ`
     - `SPXL`, `SPYU`, `UPRO`, `SSO`, `SPXU`, `SPXS`, `SDS` → `SPY`
     - Direct symbols: `SPX`, `QQQ`, `SPY` (no mapping needed)

4. **Delta Selection**
   - Determines target delta based on volatility:
     - **High Volatility** (ORB range ≥ 0.50%): 0.25 delta
     - **Moderate Volatility** (ORB range ≥ 0.35%): 0.20 delta
     - **Lower Volatility** (ORB range < 0.35%): 0.15 delta

5. **Spread Width Selection**
   - Determines spread width based on symbol:
     - **SPX**: $5-$10 spreads
     - **QQQ/SPY**: $1-$2 spreads
     - **Other Symbols**: $1-$2 spreads

6. **Momentum Score Calculation**
   - Calculates Momentum Strength Score (0-100)
   - Based on MACD, RS vs SPY, VWAP distance, volume

7. **Priority Ranking**
   - Ranks signals by priority score (eligibility + momentum)
   - Assigns priority ranks (1 = highest)

8. **Capital Allocation**
   - Allocates capital based on priority tier:
     - **Tier 1** (Top 3): 35% of allocated capital each
     - **Tier 2** (Next 5): 20% of allocated capital each
     - **Tier 3** (Remaining): 10% of allocated capital each

### **Signal Structure**

```python
DTE0Signal:
  - symbol: str (SPX, QQQ, or SPY)
  - direction: str ('LONG' or 'SHORT')
  - orb_signal: Dict (original ORB signal)
  - eligibility_result: ConvexEligibilityResult
  - target_delta: float (0.15-0.30)
  - spread_width: float ($1-$10)
  - spread_type: str ('debit', 'credit', 'lotto')
  - strategy_type: str ('debit_spread', 'lotto', etc.)
  - priority_score: float (0.0-1.0)
  - priority_rank: int (1 = highest)
  - capital_allocated: float
  - momentum_score: float (0-100)
```

---

## 🔗 **Options Chain Management**

### **Chain Fetching**

The `OptionsChainManager` fetches options chains for:
- **SPX**: Index options (cash-settled)
- **QQQ**: ETF options (physical settlement)
- **SPY**: ETF options (physical settlement)

### **Liquidity Requirements**

- **Minimum Open Interest**: 100 contracts
- **Maximum Bid-Ask Spread**: 5% of mid price
- **Minimum Volume**: 50 contracts (intraday)

### **Strike Selection**

1. **Target Delta**: Based on volatility (0.15-0.30)
2. **Strike Search**: Finds strikes closest to target delta
3. **Spread Construction**: Builds debit spread with target width
4. **Liquidity Check**: Verifies both legs meet liquidity requirements

### **Debit Spread Structure**

- **Long Leg**: Higher delta (closer to ATM)
- **Short Leg**: Lower delta (further OTM)
- **Debit Cost**: Net premium paid (long leg - short leg)
- **Max Profit**: Spread width - debit cost
- **Max Loss**: Debit cost (limited risk)

---

## 💼 **Trade Execution**

### **Execution Modes**

#### **Demo Mode** (Default)

- **Mock Executor**: `MockOptionsExecutor`
- **Account Balance**: $5,000 (separate from ORB Strategy)
- **Simulation**: Realistic P&L tracking with market data
- **Persistence**: GCS (`demo_account/mock_options_history.json`)

#### **Live Mode**

- **Broker API**: E*TRADE Options API
- **Real Execution**: Actual options trades
- **Account Management**: Separate 0DTE account support
- **OAuth Integration**: Secure token management

### **Execution Process**

1. **Position Size Validation**
   - Checks position cost ≤ 35% of account equity
   - Validates against max position limits

2. **Options Chain Analysis**
   - Fetches current options chain
   - Selects strikes based on target delta
   - Verifies liquidity requirements

3. **Order Placement**
   - **Demo**: Simulates order execution
   - **Live**: Places order via E*TRADE API

4. **Position Tracking**
   - Creates `OptionsPosition` object
   - Tracks entry price, entry time, quantity
   - Monitors current value, unrealized P&L

5. **Alert Notification**
   - Sends Options Execution alert
   - Includes position details, capital deployed

---

## 🛑 **Exit Framework**

### **Exit Priority Order**

1. **Fail-Safe** (highest priority - emergency exit)
2. **Hard Stop** (premium-based protection)
3. **Invalidation Stop** (structural stop)
4. **Time Stop** (theta decay prevention)
5. **Profit Targets** (partial and runner exits)

### **Exit Types**

#### **1. Hard Stop**

- **Debit Spreads**: -45% of premium
- **Lotto Sleeves**: -55% of premium
- **Purpose**: Limits losses on adverse moves

#### **2. Invalidation Stop**

Triggers if **ANY** occur:
- **VWAP Reclaim**: Price reclaims VWAP against position
- **ORB Midpoint Reclaim**: Price reclaims ORB midpoint
- **ORB Breakdown Retracement**: Full retracement of breakdown
- **Momentum Shift**: Significant momentum reversal

#### **3. Time Stop**

- **Debit Spreads**: 25 minutes (no favorable move)
- **Lotto Sleeves**: 12 minutes (no impulse move)
- **Purpose**: Prevents theta decay from eroding profits

#### **4. Fail-Safe**

Triggers if **ANY** occur:
- **Price Drop**: -60% of premium
- **Liquidity Degradation**: Bid-ask spread > 10%
- **Spread Widening**: Spread widens >50% (for debit spreads)

#### **5. Profit Targets**

- **First Target**: +60% → Sell 50% of position
- **Second Target**: +120% → Sell 25% of remaining position
- **Runner**: Trails until VWAP/ORB reclaim or time cutoff

---

## 🛡️ **Risk Management**

### **Position Limits**

- **Max Concurrent Positions**: 15 (matches ORB Strategy)
- **Max Position Size**: 35% of account equity per position
- **Capital Allocation**: Tiered based on priority ranking

### **Stop Losses**

- **Debit Spread Hard Stop**: -45% of premium
- **Debit Spread Time Stop**: 25 minutes
- **Debit Spread Fail-Safe**: -60% absolute stop
- **Lotto Hard Stop**: -55% of premium
- **Lotto Time Stop**: 12 minutes
- **Lotto Fail-Safe**: -60% absolute stop

### **Risk Controls**

- **Red Day Protection**: No trades on Red Days (portfolio-level)
- **Liquidity Requirements**: Minimum OI, volume, max spread
- **Position Size Validation**: Pre-execution checks
- **Capital Allocation Limits**: Tiered allocation prevents over-concentration

---

## 💰 **Profit Management**

### **Automated Profit Targets**

#### **First Target: +60%**

- **Action**: Sell 50% of position
- **Purpose**: Lock in profits, reduce risk
- **Status**: Position moves to 'partial' status

#### **Second Target: +120%**

- **Action**: Sell 25% of remaining position
- **Purpose**: Further profit locking
- **Status**: Position remains 'partial'

#### **Runner**

- **Action**: Trails remaining position
- **Exit Conditions**:
  - VWAP reclaim
  - ORB midpoint reclaim
  - Time cutoff (near EOD)
- **Purpose**: Capture extended moves

### **Profit Calculation**

- **Debit Spreads**: Profit = (current_value - entry_price) / entry_price
- **Credit Spreads**: Profit = (entry_price - current_value) / entry_price
- **Lotto Sleeves**: Profit = (current_value - entry_price) / entry_price

---

## 📏 **Position Sizing**

### **Capital Allocation**

Capital is allocated based on **priority ranking**:

- **Tier 1** (Top 3 signals): 35% of allocated capital each
- **Tier 2** (Next 5 signals): 20% of allocated capital each
- **Tier 3** (Remaining signals): 10% of allocated capital each

### **Position Size Validation**

- **Max Position Cost**: ≤ 35% of account equity
- **Total Capital Deployed**: Sum of all positions ≤ available capital
- **Pre-Execution Check**: Validates before order placement

### **Account Equity**

- **Demo Mode**: $5,000 starting balance (separate from ORB Strategy)
- **Live Mode**: Actual account balance from broker

---

## 🎯 **Priority Ranking System**

### **Priority Score Calculation**

Priority score combines:
- **Eligibility Score** (from Convex Filter): 0.0-1.0
- **Momentum Score**: 0-100 (normalized to 0.0-1.0)

```
Priority Score = (Eligibility Score × 0.70) + (Momentum Score × 0.30)
```

### **Momentum Score Components**

- **MACD Histogram**: Positive = bullish momentum
- **RS vs SPY**: Relative strength vs SPY
- **VWAP Distance**: Distance from VWAP
- **Volume Ratio**: Current volume vs average

### **Ranking Assignment**

Signals are ranked by priority score (descending):
- **Rank 1**: Highest priority score
- **Rank 2**: Second highest
- **Rank N**: Lowest priority score

### **Capital Allocation by Rank**

- **Rank 1-3**: Tier 1 (35% each)
- **Rank 4-8**: Tier 2 (20% each)
- **Rank 9+**: Tier 3 (10% each)

---

## 📊 **Performance Tracking**

### **Daily Stats**

- Positions opened
- Positions closed
- Total P&L
- Winning trades / Losing trades
- Best trade / Worst trade

### **Weekly Stats**

- Weekly P&L tracking
- Win rate
- Average P&L per trade

### **Priority Optimizer Integration**

- **Signal Collection**: Records all signals (eligible and rejected)
- **Execution Results**: Tracks executed positions and filtered signals
- **89-Point Data**: Comprehensive trade data for analysis
- **Daily Persistence**: Saves to GCS for analysis

---

## 🔄 **Integration with ORB Strategy**

### **Signal Flow**

1. **ORB Strategy** generates signals
2. **0DTE Strategy** receives signals via `listen_to_orb_signals()`
3. **Convex Filter** filters signals
4. **0DTE Signals** generated and ranked
5. **Execution** via Options Trading Executor
6. **Position Management** via Options Exit Manager

### **Alert Integration**

- **Signal Collection**: Included in the **unified Trade Signal Collection alert** (SO + 0DTE sections)
- **Execution**: Separate Options Execution alerts
- **Exits**: Individual and aggregated exit alerts
- **EOD**: Separate Options EOD report

---

## ⚙️ **Configuration**

See `easy0DTE/configs/0dte.env` for full configuration options.

---

*Last Updated: April 14, 2026*  
*Version: Rev 00349 (+ Rev 00238)*  
*Maintained by: Easy Trading Software Team*

