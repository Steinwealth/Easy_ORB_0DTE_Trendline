# Easy 0DTE Strategy - Comprehensive Guide

**Last Updated**: April 14, 2026  
**Version**: **Rev 00349** (ORB 0DTE fast monitor + execution reliability tuning; Hard Gate no max-ORB reject; structured Cloud Logging `0DTE_*` / `CONVEX_REJECT_DETAIL`; watchlist trims; **ETHU**/**ETHD** mapping). **2.38.0 (Rev 00238)** — real-time options pricing, long-call optimization.  
**Status**: ✅ **PRODUCTION ACTIVE** - Integrated with Easy ORB Strategy

---

## 🎯 **Overview**

The **Easy 0DTE Strategy** is a sophisticated options trading system that works in conjunction with the Easy ORB Strategy to provide selective convex amplification of high-conviction ORB signals. Unlike traditional leverage, 0DTE options offer gamma exposure that can generate outsized returns on short-term directional moves.

**Core Philosophy**: *Not every ORB-qualified trade gets options—only the highest-conviction setups.*

**Easy 0DTE = Selective Convex Amplification. Gamma > Leverage.**

---

## 🏗️ **Architecture**

The Easy 0DTE Strategy is integrated into the Easy ORB Strategy system and operates as a **filtered overlay**:

```
ORB Strategy Signals
    ↓
Convex Eligibility Filter (Selective Filtering)
    ↓
0DTE Signal Generation (SPX, QQQ, SPY)
    ↓
Options Chain Analysis & Strike Selection
    ↓
Trade Execution (Debit Spreads, Credit Spreads, Lotto Sleeves)
    ↓
Position Management & Exit Framework
```

### **Key Components**

1. **Prime0DTEStrategyManager**: Listens to ORB signals and generates 0DTE signals
2. **ConvexEligibilityFilter**: Filters ORB signals to determine options eligibility
3. **OptionsChainManager**: Manages options chains and strike selection
4. **OptionsTradingExecutor**: Executes trades (Demo/Live modes)
5. **OptionsExitManager**: Comprehensive exit framework (hard stops, time stops, profit targets)
6. **MockOptionsExecutor**: Demo mode simulation (separate $5,000 account)

---

## 🎯 **Target Symbols**

**Priority Order**: Ranked from full `0dte_list.csv` universe (core index/ETF names remain highest-priority when qualified)

- **Tier 1 (9)**: Core daily 0DTE names (SPX, SPY, QQQ, IWM, MAGS, VIX, IBIT, GLD, SLV)
- **Tier 2 (71)**: Equities and sector/thematic momentum names

**Symbol Mapping**: ORB signals from leveraged ETFs (TQQQ, SPXL, UPRO, etc.) are mapped to their underlying 0DTE symbols (QQQ, SPY).

---

## 🔍 **Convex Eligibility Filter**

The Convex Eligibility Filter determines which ORB signals deserve options exposure. **All criteria must pass**:

### **Eligibility Criteria**

1. **Volatility Score** (40% weight)
   - Must be ≥ Top 20% percentile (80th percentile)
   - Lower threshold for leveraged ETFs (60th percentile)

2. **ORB Range OR 5-min ATR** (25% weight)
   - ORB range ≥ 0.35% of symbol price **OR**
   - 5-minute ATR ≥ intraday minimum threshold (0.25% of price)

3. **Red Day / regime** (15% weight) — **direction-aware** (Rev 00246+)
   - Portfolio Red Day blocks **ORB Long** and **0DTE Long (CALL)**; **0DTE Short (PUT)** can remain eligible
   - Convex scoring uses a neutral/regime pass where configured (see main `Strategy.md` / `convex_eligibility_filter`)

4. **ORB Break** (Required)
   - **LONG**: Price > ORB High (breakout)
   - **SHORT**: Price < ORB Low (breakdown)

5. **Volume Confirmation** (Required)
   - Volume > ORB volume average

6. **VWAP Condition** (Required)
   - **LONG**: Price ≥ VWAP
   - **SHORT**: Price ≤ VWAP

7. **Momentum Confirmation** (10% weight)
   - Positive MACD histogram OR positive RS vs SPY

8. **Market Regime** (10% weight)
   - Must be trend/impulse day (NOT rotation)
   - Inferred from VWAP distance and RS vs SPY

### **Minimum Eligibility Score**: 0.75 (75%)

---

## 📊 **Strategy Types**

### **Level 2 Strategies** (Current Implementation)

1. **Debit Spreads** (Primary)
   - **Call Debit Spreads**: Bullish (LONG ORB signals)
   - **Put Debit Spreads**: Bearish (SHORT ORB signals)
   - Target Delta: 0.15-0.30 (10-30 delta for gamma explosion)
   - Spread Width: $1-$2 (QQQ/SPY), $5-$10 (SPX)

2. **Lotto Sleeves** (Optional, Currently Disabled)
   - Single-leg options (OTM calls/puts)
   - Target Delta: 0.15
   - Max Cost: $200 per position

### **Future Strategies** (Level 3+)

- Credit Spreads (require Level 3+ approval)
- Advanced multi-leg strategies

---

## 💰 **Position Sizing & Risk Management**

### **Position Limits**

- **Max Concurrent Positions**: 15 (matches ORB Strategy)
- **Max Position Size**: 35% of account equity per position (matches ORB Strategy)
- **Capital Allocation**: Based on priority ranking (tiered allocation)

### **Risk Parameters**

- **Debit Spread Hard Stop**: -45% of premium
- **Debit Spread Time Stop**: 25 minutes (no favorable move)
- **Debit Spread Fail-Safe**: -60% absolute stop
- **Lotto Hard Stop**: -55% of premium
- **Lotto Time Stop**: 12 minutes (no impulse move)
- **Lotto Fail-Safe**: -60% absolute stop

---

## 🎯 **Profit Management**

### **Automated Profit Targets**

1. **First Target**: +60% → Sell 50% of position
2. **Second Target**: +120% → Sell 25% of remaining position
3. **Runner**: Trails until VWAP/ORB reclaim or time cutoff

### **Exit Framework**

**Priority Order**:
1. **Fail-Safe** (highest priority - emergency exit)
2. **Hard Stop** (premium-based protection)
3. **Invalidation Stop** (structural stop - VWAP/ORB reclaim)
4. **Time Stop** (theta decay prevention)
5. **Profit Targets** (partial and runner exits)

---

## 🔄 **Trading Modes**

### **Demo Mode** (Default)

- **Account Balance**: $5,000 (separate from ORB Strategy's $1,000)
- **Mock Execution**: Simulated trades with realistic P&L tracking
- **Data Persistence**: GCS (`demo_account/mock_options_history.json`)
- **Position Tracking**: Full position lifecycle simulation

### **Live Mode**

- **Broker Integration**: E*TRADE Options API
- **Real Execution**: Actual options trades
- **Account Management**: Separate 0DTE account support
- **OAuth Integration**: Secure token management

---

## 📈 **Signal Generation Workflow**

**0DTE produces both Long (CALL) and Short (PUT) signals** — unlike ORB SO, which is Long-only. The combined list is ranked and the top N (max 15) are executed as options.

1. **ORB Signal Reception**: Receives ORB breakout (LONG) and breakdown (SHORT) signals from PrimeORBStrategyManager
2. **Convex Filtering**: Applies Convex Eligibility Filter (direction-aware: LONG vs SHORT, e.g. Red Day)
3. **Signal Mapping**: Maps ORB symbols to 0DTE symbols (TQQQ → QQQ, SPXL → SPY)
4. **Delta Selection**: Determines target delta based on volatility (0.15-0.35)
5. **Spread Width Selection**: Determines spread width ($1-$10 based on symbol/volatility)
6. **Priority Ranking**: Ranks **all** 0DTE signals (CALL + PUT) by priority score (eligibility + momentum)
7. **Capital Allocation**: Allocates capital based on priority tier (top 15 combined)
8. **Signal Output**: Generates DTE0Signal objects (direction LONG or SHORT) for execution as CALL or PUT options

### **Hard Gate vs chain liquidity (Rev 00326)**

- **Hard Gate** (before the execution queue): eligible **symbol / 0DTE target** allowlist, **session time window**, **volume / volume_ratio** checks. **Very wide ORB range** → **warning log only** (no Hard Gate reject on maximum ORB % — consistent with 0DTE priority favoring wider range).
- **Execution / strike selection**: Delta, premium, and **options-chain** guardrails (e.g. open interest, bid/ask spread, volume on contracts — settings such as `0DTE_OPTIONS_*` in `0dte.env`).

### **Cloud log grep (Rev 00326)**

Trace drops after Convex: `0DTE_PIPELINE`, `0DTE_TARGET_FILTER`, `0DTE_DEDUPE`, `0DTE_PRIORITY_DROP`, `0DTE_HARD_GATE_REJECT`, `0DTE_EXEC_REJECT`, `0DTE_EXEC_STAGE`, `CONVEX_REJECT_DETAIL`, `0DTE_CONVEX_STAGE` (plus existing `CONVEX_FILTER`, `SO_PIPELINE` on the SO path).

---

## 🔔 **Integration with ORB Strategy**

The 0DTE Strategy is **fully integrated** into the ORB Strategy system:

- **Signal Collection**: Included in the **unified Trade Signal Collection alert** with SO + 0DTE sections (no separate 0DTE-only collection alert)
- **Execution Alerts**: Separate Options Execution alerts
- **Exit Alerts**: Individual and aggregated exit alerts
- **EOD Reports**: Separate Options EOD report

---

## 📊 **Data Collection**

### **Priority Optimizer Integration**

- **Signal Collection**: Records all 0DTE signals (eligible and rejected)
- **Execution Results**: Tracks executed positions and filtered signals
- **89-Point Data**: Comprehensive trade data for analysis
- **Daily Persistence**: Saves to GCS for analysis

---

## ⚙️ **Configuration**

Configuration is managed via `easy0DTE/configs/0dte.env`:

```env
# Enable 0DTE Strategy
ENABLE_0DTE_STRATEGY=true

# Trading Mode
ETRADE_MODE=demo
DEMO_MODE_ENABLED=true

# Convex Eligibility Filter
0DTE_CONVEX_VOLATILITY_PERCENTILE=0.80
0DTE_CONVEX_ORB_RANGE_MIN=0.35
0DTE_CONVEX_MOMENTUM_REQUIRED=true
0DTE_CONVEX_TREND_DAY_REQUIRED=true
0DTE_CONVEX_MIN_SCORE=0.75

# Position Limits
0DTE_MAX_POSITIONS=6
0DTE_MAX_POSITION_SIZE_PCT=0.35

# Debit Spread Settings
0DTE_DEBIT_SPREAD_TARGET_DELTA_MIN=0.30
0DTE_DEBIT_SPREAD_TARGET_DELTA_MAX=0.45
0DTE_DEBIT_SPREAD_WIDTH_OPTIONS=1.0,2.0

# Profit Management
0DTE_AUTO_PARTIAL_ENABLED=true
0DTE_FIRST_PROFIT_TARGET_PCT=0.60
0DTE_FIRST_PROFIT_SELL_PCT=0.50
0DTE_SECOND_PROFIT_TARGET_PCT=1.20
0DTE_SECOND_PROFIT_SELL_PCT=0.25
```

---

## 📚 **Documentation**

- **[Strategy.md](./Strategy.md)**: Detailed strategy documentation
- **[Data.md](./Data.md)**: Broker data connections and symbol list management (Rev 00238; counts/logging Rev 00326)
- **[Alerts.md](./Alerts.md)**: Complete alert types and formats

---

## 🔗 **Related Documentation**

- **Easy ORB Strategy**: `../README.md`
- **ORB Strategy Docs**: `../docs/`
- **Priority Optimizer**: `../priority_optimizer/`

---

## ✅ **Status**

**Current Version**: Rev 00349 (+ Rev 00238 baseline)  
**Deployment**: ✅ Active (Integrated with ORB Strategy)  
**Trading Mode**: Demo (Default)  
**Symbol list**: **80** rows in `0dte_list.csv` (Tier 1: 9, Tier 2: 71); ORB `core_list.csv` **129** rows — shared ORB capture ~**205** unique symbols  
**Last Updated**: April 14, 2026

---

*Maintained by: Easy Trading Software Team*

