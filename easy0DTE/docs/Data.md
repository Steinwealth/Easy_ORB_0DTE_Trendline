# Data Management System
## Easy 0DTE Strategy - Options Data Architecture

**Last Updated**: April 10, 2026  
**Version**: **Rev 00326** (symbol counts, Hard Gate vs execution clarity, Cloud log grep tokens, watchlist / **ETHU**–**ETHD**). **Rev 00238** (Real-Time Options Price Tracking + Long Call Optimization).  
**Purpose**: Comprehensive documentation of the data management system for the Easy 0DTE Strategy. The system uses E*TRADE API exclusively for all options data, including options chains, real-time quotes, and position pricing. All data comes from the configured broker - no third-party data sources.

**Current Focus**: 0DTE Options Trading with Real-Time Price Tracking + structured pipeline logging  
**Status**: ✅ Production Ready - Real-Time Options Price Tracking (Rev 00238), Long Call Optimization (Rev 00238), Enhanced Red Day Detection (Rev 00237), Broker-Only Data Source (Rev 00236), 0DTE diagnostics (Rev 00326)

---

## 📋 **Table of Contents**

1. [Data Architecture Overview](#data-architecture-overview)
2. [Broker Data Connections](#broker-data-connections)
3. [Symbol List Management](#symbol-list-management)
4. [Options Chain Data](#options-chain-data)
5. [Real-Time Options Price Tracking](#real-time-options-price-tracking)
6. [ORB Data Collection](#orb-data-collection)
7. [Data Flow & Processing](#data-flow--processing)
8. [Performance Optimization](#performance-optimization)
9. [Integration with ORB Strategy](#integration-with-orb-strategy)

---

## ✅ **DEPLOYMENT STATUS (Rev 00238 - January 9, 2026)**

**Easy 0DTE Strategy Deployed & Operational:**
- ✅ **Symbol List**: Dynamic (**80** data rows in `0dte_list.csv` — fully scalable)
- ✅ **Broker Data**: E*TRADE API exclusively (no third-party fallback)
- ✅ **Options Chain Fetching**: Real-time from E*TRADE for entry execution
- ✅ **Real-Time Price Tracking**: Options prices updated every 30 seconds for exit decisions (Rev 00238)
- ✅ **ORB Data Collection**: All 0DTE symbols included in ORB capture (6:30-6:45 AM PT)
- ✅ **Signal Collection**: 0DTE signals generated from ORB signals during signal collection (7:15-7:30 AM PT)
- ✅ **Entry Execution**: Options trades executed at 7:30 AM PT with real-time chain data
- ✅ **Position Monitoring**: Real-time options prices tracked every 30 seconds (Rev 00238)
- ✅ **Exit Decisions**: Based on actual options P&L, not underlying price movement (Rev 00238)
- ✅ **Long Call Optimization**: Cheap OTM options (delta 0.15) selected for maximum gamma explosion (Rev 00238)

---

## 🏗️ **Data Architecture Overview**

The Easy 0DTE Strategy implements a **broker-only data management system** that exclusively uses E*TRADE API for all options data. The system ensures accurate entry execution and real-time position monitoring through direct broker integration.

### **Key Principles**
- **Broker-Only**: All options data from E*TRADE API (no third-party sources)
- **Real-Time**: Options prices updated every 30 seconds for accurate exit decisions
- **Reliability**: Direct broker integration ensures data accuracy
- **Scalability**: Dynamic symbol list (add/remove without code changes)
- **Performance**: Optimized for low latency and efficient API usage

### **Data Flow**

```
ORB Signal Collection (7:15-7:30 AM PT)
    ↓
0DTE Signal Generation (from ORB signals)
    ↓
Options Chain Fetching (E*TRADE API) - Entry Execution
    ↓
Trade Execution (with real-time chain data)
    ↓
Position Monitoring (every 30 seconds)
    ↓
Real-Time Options Price Tracking (E*TRADE API) - Exit Decisions (Rev 00238)
    ↓
Exit Execution (based on actual options P&L)
```

---

## 🔌 **Broker Data Connections**

### **E*TRADE Options API Integration**

The 0DTE Strategy uses **E*TRADE Options API** exclusively for all options data operations:

#### **1. Options Chain Fetching**

**Purpose**: Fetch options chains for entry execution and strike selection

**API Endpoint**: `/v1/market/optionchains`

**Implementation**: `ETradeOptionsAPI.fetch_options_chain()`

**Parameters**:
- `symbol`: Underlying symbol (e.g., QQQ, SPY, IWM)
- `expiry`: Expiry date (YYYYMMDD format, defaults to today for 0DTE)
- `strike_count`: Number of strikes above/below ATM (default: 20)
- `include_greeks`: Include Greeks (delta, gamma, theta, vega, IV)

**Returns**:
```python
{
    'calls': [ETradeOptionContract, ...],
    'puts': [ETradeOptionContract, ...]
}
```

**Each Option Contract Contains**:
- `symbol`: Underlying symbol
- `strike`: Strike price
- `expiry`: Expiry date
- `option_type`: 'CALL' or 'PUT'
- `bid`: Current bid price
- `ask`: Current ask price
- `last`: Last trade price
- `volume`: Trading volume
- `open_interest`: Open interest
- `delta`: Delta (price sensitivity)
- `gamma`: Gamma (delta sensitivity)
- `theta`: Theta (time decay)
- `vega`: Vega (volatility sensitivity)
- `implied_volatility`: Implied volatility
- `mid_price`: Calculated mid price (bid + ask) / 2

**Usage**:
- Fetched during entry execution (7:30 AM PT)
- Used for strike selection based on target delta and premium range
- Includes full Greeks for strategy selection

#### **2. Real-Time Options Quote Fetching** (Rev 00238)

**Purpose**: Fetch current bid/ask for specific option contracts during position monitoring

**API Endpoint**: `/v1/market/optionchains` (filtered for specific strike)

**Implementation**: `ETradeOptionsAPI.get_option_quote()`

**Parameters**:
- `symbol`: Underlying symbol
- `strike`: Strike price
- `expiry`: Expiry date (YYYYMMDD format)
- `option_type`: 'CALL' or 'PUT'

**Returns**:
```python
{
    'bid': float,
    'ask': float,
    'last': float,
    'mid_price': float,
    'volume': int,
    'open_interest': int,
    'delta': float,
    'gamma': float,
    'theta': float,
    'vega': float,
    'implied_volatility': float
}
```

**Usage**:
- Fetched every 30 seconds during position monitoring
- Updates `position.current_value` with real options prices
- Enables accurate exit decisions based on actual options P&L

**Example**:
- QQQ 628c position: Fetches current bid/ask for QQQ 628 CALL expiring today
- Position value updated from $0.19 → $0.97 based on real quote (not underlying QQQ price)

#### **3. Authentication & Security**

**OAuth Integration**:
- Uses same OAuth authentication as ETF trading (`PrimeETradeTrading`)
- Secure token management via E*TRADE OAuth flow
- Token refresh handled automatically

**API Access**:
- Requires E*TRADE Options Trading account
- Account must have options trading permissions enabled
- API rate limits: Standard E*TRADE API limits apply

#### **4. Error Handling**

**Fallback Behavior**:
- If options chain fetch fails: No trade executed (signal skipped)
- If quote fetch fails: Position value remains unchanged (retry next cycle)
- If API unavailable: System logs error and continues with available data

**Rate Limiting**:
- Options chain fetches cached for 5-10 seconds to reduce API calls
- Quote fetches batched where possible (not currently implemented)
- Position monitoring cycles at 30-second intervals

---

## 📊 **Symbol List Management**

### **0DTE Symbol List**

**File**: `data/watchlist/0dte_list.csv`

**Structure**:
```csv
symbol,tier,category,notes
SPX,1,index,"S&P 500 Index - Cash-settled, pro mode (Core Daily 0DTE)"
QQQ,1,etf,Invesco QQQ Trust - Primary 0DTE target (Core Daily 0DTE)
...
```

**Current Status**: **82** symbols total (Tier 1: 9, Tier 2: 73)

**Watchlist / mapping (Rev 00326):** `core_list.csv` ORB universe is **136** data rows. Sentiment and inverse wiring live under `data/watchlist/` (`complete_sentiment_mapping.json`, `sentiment_pairs_mapping.json`, `orb_inverse_mapping.json`) — Ethereum pair **ETHU** (bull) / **ETHD** (bear); legacy **ETHT** removed.

### **Cloud log grep — 0DTE pipeline (Rev 00326)**

`0DTE_PIPELINE`, `0DTE_TARGET_FILTER`, `0DTE_DEDUPE`, `0DTE_PRIORITY_DROP`, `0DTE_HARD_GATE_REJECT`, `0DTE_EXEC_REJECT`, `0DTE_EXEC_STAGE`, `CONVEX_REJECT_DETAIL`, `0DTE_CONVEX_STAGE` (plus `CONVEX_FILTER`, `SO_PIPELINE`).

### **Tier Organization**

#### **Tier 1: Core Daily 0DTE (9 symbols)**

**Core Symbols** (9):
- SPX, SPY, QQQ, IWM, MAGS, VIX, IBIT, GLD, SLV

**Leverage ETFs** (14):
- **S&P 500**: SPYU (4x), SPXL (3x), UPRO (3x)
- **Nasdaq**: TQQQ (3x)
- **Semiconductors**: SOXL (3x)
- **Russell/Small/MidCap**: URTY (3x), TNA (3x), UMDD (3x)
- **Sectors**: TECL (3x Technology), FAS (3x Financial), GUSH (3x Oil & Gas), LABU (3x Biotech), FNGU (3x FANG+), WEBL (3x Internet)

#### **Tier 2: Equities & sector ETFs (73 symbols)**

Single-stock **2x** names are dropped when the **underlying equity** is already on the list; if the underlying was missing, it is added (e.g. **LRCX**, **FLY**, **CRDO**, **APP** replace **LRCU**, **FLYT**, **CRDU**, **APPX**).

Organized by category:
1. **MAG-7 + Core Tech**: NVDA, AMD, TSLA, META, AMZN, APP, AAPL, MSFT, GOOGL
2. **AI/SEMI Leaders**: AVGO, ASML, ARM, SMCI, MRVL, AMAT, INTC, LRCX
3. **Pharma / Platform / Cloud / Retail**: LLY, NOW, SNOW, NFLX, COST, HD, MSTR
4. **ETFs**: SMH, GDX
5. **Crypto/FinTech**: COIN, HOOD
6. **AI/Cloud Leaders**: PLTR, CRWD, NET
7. **Semiconductor/Infrastructure**: QCOM, MU, SNDK, VLN, AEHR, TTMI
8. **Energy/Infrastructure**: CEG, VST, PWR, BLDR, BE
9. **Data Centers/Cloud**: EQIX, CRWV, CRDO, APLD, NBIS
10. **Aerospace/Defense/Nuclear**: OKLO, KTOS, RKLB, FLY, ASTS
11. **Real Estate**: OPEN, RGC
12. **Biotech**: CGON, RVMD
13. **High-Beta/Retail**: SOFI, HIMS, DAL, AAL, RGTI
14. **Crypto Miners**: IREN, CIFR, CLSK, WULF
15. **Homebuilders**: BZH, LEN, DHI, TOL, PHM
16. **Storage**: WDC

### **Symbol Loading**

**Implementation**: `load_0dte_symbols()` in `prime_0dte_strategy_manager.py`

**Process**:
1. Load symbols from `data/watchlist/0dte_list.csv`
2. Parse tier information
3. Sort by tier (Tier 1 first, then Tier 2)
4. Maintain order within tiers
5. Return sorted list for signal generation

**Fallback**:
- If file not found: Uses default `['SPX', 'QQQ', 'SPY']`
- Logs warning if default used

### **Symbol Priority**

Symbols are processed in tier order during signal generation:
- **Tier 1**: Highest priority (processed first)
- **Tier 2**: Secondary priority (processed after Tier 1)

Within each tier, symbols maintain CSV order (as specified by user).

---

## 📈 **Options Chain Data**

### **Options Chain Manager**

**Purpose**: Manages options chains for strike selection and trade execution

**Implementation**: `OptionsChainManager` in `options_chain_manager.py`

**Key Methods**:
1. `fetch_options_chain()`: Fetch chain from E*TRADE API
2. `select_debit_spread_strikes()`: Select optimal strikes for debit spreads
3. `select_lotto_strike()`: Select strike for single-leg positions (long calls/puts)
4. `validate_liquidity()`: Validate bid/ask spread and open interest

### **Strike Selection Logic**

#### **Debit Spreads** (Primary Strategy)

**Target Delta**: 0.15-0.25 (10-25 delta for gamma explosion)

**Premium Range**: $0.15-$0.60 (Rev 00238: lowered from $0.20)

**Strike Position**: 1-3 strikes OTM (out of the money)

**Spread Width**:
- **QQQ/SPY**: $1-$2
- **SPX**: $5-$10
- **Other**: $1-$2 (default)

**Filtering**:
1. Filter by delta range (target ± 0.05)
2. Filter by premium range ($0.15-$0.60)
3. Filter by strike position (1-3 strikes OTM)
4. Validate liquidity (bid/ask spread ≤ 5%, open interest ≥ 100)

#### **Long Calls/Puts** (High Momentum Strategy - Rev 00238)

**Trigger**: Momentum ≥ 80 AND ORB range ≥ 0.40%

**Target Delta**: 0.15 (cheap OTM options for maximum gamma explosion)

**Premium Range**: $0.15-$0.60

**Strike Position**: 1-3 strikes OTM

**Example**:
- QQQ 628c @ $0.19 (delta ~0.12, ~1% OTM)
- If QQQ moves +0.86%, option moves +410% ($0.19 → $0.97)

### **Chain Caching**

**Purpose**: Reduce API calls and improve performance

**Implementation**: In-memory cache with TTL (Time To Live)

**Cache Duration**: 5-10 seconds (options prices change rapidly)

**Cache Key**: `{symbol}_{expiry}_{strike_count}`

**Invalidation**: Automatic after TTL expires

---

## 🔄 **Real-Time Options Price Tracking** (Rev 00238)

### **Position Value Updates**

**Purpose**: Update open positions with real-time options prices for accurate exit decisions

**Implementation**: `update_positions_with_real_prices()` in `options_trading_executor.py`

**Process**:
1. Get all open positions
2. For each position:
   - Determine position type (lotto, debit spread, credit spread)
   - Fetch current options quote from E*TRADE API
   - Calculate current position value based on position type
   - Update `position.current_value`
3. Log updates for monitoring

**Frequency**: Every 30 seconds (during position monitoring loop)

### **Position Value Calculation**

#### **Single-Leg Positions** (Long Calls/Puts, Lotto)

**Calculation**: `current_value = quote.mid_price`

**Example**:
- QQQ 628c position: Fetch quote for QQQ 628 CALL
- Current value = mid_price from quote ($0.19, $0.97, etc.)

#### **Debit Spreads**

**Calculation**: `current_value = long_leg_value - short_leg_value`

**Process**:
1. Fetch quote for long leg (e.g., QQQ 628c)
2. Fetch quote for short leg (e.g., QQQ 629c)
3. Calculate spread value: long_leg.mid_price - short_leg.mid_price

#### **Credit Spreads**

**Calculation**: `current_value = cost_to_close = short_leg_value - long_leg_value`

**Process**:
1. Fetch quote for short leg (higher strike)
2. Fetch quote for long leg (lower strike)
3. Calculate cost to close: short_leg.mid_price - long_leg.mid_price

### **Integration with Monitoring Loop**

**Location**: `prime_trading_system.py` (main trading loop)

**Timing**: Before calling `monitor_positions()`

**Code Flow**:
```python
# Rev 00238: Update positions with real-time options prices BEFORE monitoring
# This ensures exit decisions are based on actual options P&L, not underlying price
await self.dte0_manager.options_executor.update_positions_with_real_prices()

# Monitor options positions
exit_signals = await self.dte0_manager.options_executor.monitor_positions(
    market_data_provider=get_market_data,
    orb_data_provider=get_orb_data
)
```

**Impact**: Exit decisions (profit targets, hard stops) now based on actual options P&L

---

## 📊 **ORB Data Collection**

### **ORB Capture for 0DTE Symbols**

**Purpose**: Collect ORB data for all 0DTE symbols to enable signal generation

**Implementation**: `_capture_orb_for_all_symbols()` in `prime_trading_system.py`

**Process**:
1. Load ORB symbol list from `core_list.csv`
2. Load 0DTE symbol list from `0dte_list.csv`
3. Merge lists (add 0DTE symbols not already in ORB list)
4. Batch capture ORB data for all symbols (ORB + 0DTE)

**Timing**: 6:30-6:45 AM PT (ORB capture window)

**Data Source**: E*TRADE batch quotes (25 symbols per call)

**Example**:
- ORB list: **136** symbols (`core_list.csv` data rows)
- 0DTE list: **112** symbols (`0dte_list.csv` data rows)
- Combined: **~136** unique symbols for ORB capture (0DTE symbols already in ORB list are not duplicated)
- ORB capture: All symbols processed in batch

### **Signal Collection Integration**

**Purpose**: Generate 0DTE signals from ORB signals during signal collection window

**Timing**: 7:15-7:30 AM PT (same window as ORB signal collection)

**Process**:
1. ORB signals collected from SO scanning (7:15-7:30 AM PT)
2. 0DTE signals generated from ORB signals:
   - Filter through Convex Eligibility Filter
   - Map ORB symbols to 0DTE symbols (if needed)
   - Generate DTE0Signal objects
3. Signal collection alert sent with both ORB and 0DTE signals

**Integration**:
- 0DTE signals included in the **unified Trade Signal Collection alert** with SO + 0DTE (single alert)
- Separate execution alerts for ORB trades and 0DTE trades

---

## 🔄 **Data Flow & Processing**

### **Daily Trading Session Flow**

#### **1. ORB Capture (6:30-6:45 AM PT)**

```
Load Symbols (ORB + 0DTE) → E*TRADE Batch Quotes → ORB Data Captured
```

- **Symbols**: All symbols from core_list.csv + 0dte_list.csv
- **Data**: Opening range (high, low) for all symbols
- **Storage**: In-memory ORB data dictionary

#### **2. Signal Collection (7:15-7:30 AM PT)**

```
ORB Signals Collected → 0DTE Signals Generated → Signal Collection Alert
```

- **ORB Signals**: Generated from SO scanning
- **0DTE Signals**: Generated from ORB signals via Convex Eligibility Filter
- **Alert**: Single alert showing both ORB and 0DTE signals

#### **3. Entry Execution (7:30 AM PT)**

```
0DTE Signals → Options Chain Fetch (E*TRADE) → Strike Selection → Trade Execution
```

- **Options Chain**: Fetched from E*TRADE API for each symbol
- **Strike Selection**: Based on target delta, premium range, liquidity
- **Trade Execution**: Options trades executed with real-time chain data

#### **4. Position Monitoring (7:30 AM - 12:55 PM PT)**

```
Open Positions → Real-Time Quotes (E*TRADE) → Position Value Update → Exit Decisions
```

- **Real-Time Quotes**: Fetched every 30 seconds from E*TRADE API
- **Position Value Update**: Options prices update `position.current_value`
- **Exit Decisions**: Based on actual options P&L (not underlying price)

#### **5. End of Day (12:55 PM PT)**

```
All Positions → Close All → Exit Alerts
```

- **EOD Close**: All positions closed at 12:55 PM PT
- **Exit Alerts**: Position exit alerts sent

---

## ⚡ **Performance Optimization**

### **API Efficiency**

**Batch Processing**:
- Options chain fetching: Done during entry execution (one-time per symbol)
- Quote fetching: Individual calls (can be optimized with batch API if available)

**Caching**:
- Options chains: Cached for 5-10 seconds (reduces redundant calls)
- Position quotes: Fetched fresh every 30 seconds (accurate pricing required)

**Rate Limiting**:
- E*TRADE API limits: Standard limits apply
- Position monitoring: 30-second intervals (reasonable for options monitoring)
- Error handling: Retry logic with exponential backoff

### **Data Quality**

**Validation**:
- **At strike selection / execution**, chain guardrails typically include bid/ask spread, open interest, and volume thresholds (see `0DTE_OPTIONS_*` in `easy0DTE/configs/0dte.env`) — distinct from the **Hard Gate** pre-queue checks (allowlist, time window, volume ratio; Rev 00326).
- Price validation: Mid price calculated from bid/ask
- Greeks validation: All Greeks present for strategy selection

**Error Handling**:
- Missing quotes: Position value remains unchanged (retry next cycle)
- Chain fetch failures: Trade skipped (signal rejected)
- API errors: Logged and system continues

---

## 🔗 **Integration with ORB Strategy**

### **Data Sharing**

The 0DTE Strategy shares data infrastructure with the ORB Strategy:

**ORB Data**:
- All 0DTE symbols included in ORB capture
- ORB signals used as input for 0DTE signal generation
- ORB data (VWAP, volume, momentum) used in Convex Eligibility Filter

**Market Data**:
- SPY momentum: Retrieved from E*TRADE for Red Day Detection (Rev 00237)
- VIX level: Retrieved from E*TRADE for Red Day Detection (Rev 00237)
- Market alignment: Used for momentum score calculation

### **Unified Configuration**

**Configuration Files**:
- `configs/data-providers.env`: Broker configuration (E*TRADE default)
- `configs/deployment.env`: Deployment settings (GCP project, region)
- `easy0DTE/configs/0dte.env`: 0DTE-specific settings

**Shared Settings**:
- `BROKER_TYPE`: E*TRADE (default)
- `GCP_PROJECT_ID`: Google Cloud project
- `GCP_REGION`: Deployment region

---

## 📝 **Configuration**

### **0DTE-Specific Settings**

**File**: `easy0DTE/configs/0dte.env`

```env
# Enable 0DTE Strategy
ENABLE_0DTE_STRATEGY=true

# Trading Mode
ETRADE_MODE=demo
DEMO_MODE_ENABLED=true

# Options Chain Settings
0DTE_OPTIONS_MIN_OPEN_INTEREST=100
0DTE_OPTIONS_MAX_BID_ASK_SPREAD_PCT=5.0
0DTE_OPTIONS_MIN_VOLUME=50

# Strike Selection (Rev 00238)
0DTE_DEBIT_SPREAD_TARGET_DELTA_MIN=0.15
0DTE_DEBIT_SPREAD_TARGET_DELTA_MAX=0.30
0DTE_PREMIUM_MIN=0.15  # Rev 00238: Lowered from 0.20
0DTE_PREMIUM_MAX=0.60

# Long Call Settings (Rev 00238)
0DTE_LONG_CALL_TARGET_DELTA=0.15  # Cheap OTM for gamma explosion
0DTE_LONG_CALL_MOMENTUM_MIN=80.0
0DTE_LONG_CALL_ORB_RANGE_MIN=0.40

# Target Symbols
0DTE_TARGET_SYMBOLS=SPX,QQQ,SPY,IWM,MAGS,VIX,IBIT,GLD,SLV
```

### **Data Provider Settings**

**File**: `configs/data-providers.env`

```env
# Broker Configuration
BROKER_TYPE=etrade  # Default: E*TRADE

# E*TRADE Settings (if broker is E*TRADE)
ETRADE_CONSUMER_KEY=your_key
ETRADE_CONSUMER_SECRET=your_secret
ETRADE_SANDBOX=false
```

---

## 🎯 **Key Features**

### **Real-Time Options Price Tracking** (Rev 00238)

**Before** (Rev 00237):
- Exit decisions based on underlying price movement
- QQQ moves +0.86% → System sees +0.86% P&L
- **Misses**: Actual options move from $0.19 to $0.97 (+410%)

**After** (Rev 00238):
- Exit decisions based on actual options prices
- QQQ moves +0.86% → System sees options move from $0.19 to $0.97 (+410%)
- **Captures**: Real options P&L for accurate exit decisions

### **Long Call Optimization** (Rev 00238)

**Before**:
- Long calls selected ATM/ITM options (delta 0.40)
- Premium: $2-$5 (expensive)
- Less gamma explosion potential

**After**:
- Long calls select cheap OTM options (delta 0.15)
- Premium: $0.15-$0.60 (allows $0.19 entries)
- Maximum gamma explosion potential

### **Broker-Only Data Source** (Rev 00236)

**Principle**: All data must come from configured broker (E*TRADE default)

**Benefits**:
- Data accuracy: Single authoritative source
- No conflicts: No mixed data sources
- Reliability: Broker data is always current

---

## 📊 **Data Usage Examples**

### **Example 1: Entry Execution**

**Scenario**: QQQ LONG signal with momentum ≥ 80

**Data Flow**:
1. **ORB Signal**: QQQ breaks above ORB High
2. **0DTE Signal**: Generated from ORB signal (passes Convex Eligibility Filter)
3. **Options Chain**: Fetched from E*TRADE API
   - Symbol: QQQ
   - Expiry: Today's date (0DTE)
   - Include Greeks: Yes
4. **Strike Selection**: 
   - Target: Delta 0.15, Premium $0.15-$0.60
   - Selected: QQQ 628c @ $0.19 (delta 0.12, ~1% OTM)
5. **Trade Execution**: Position opened at $0.19

### **Example 2: Position Monitoring**

**Scenario**: QQQ 628c position opened at $0.19

**Data Flow** (every 30 seconds):
1. **Position Update**: `update_positions_with_real_prices()` called
2. **Quote Fetch**: E*TRADE API called for QQQ 628c
   - Symbol: QQQ
   - Strike: 628
   - Expiry: Today's date
   - Option Type: CALL
3. **Quote Received**:
   - Bid: $0.96
   - Ask: $0.98
   - Mid: $0.97
4. **Position Value Update**: `position.current_value = $0.97`
5. **P&L Calculation**: ($0.97 - $0.19) / $0.19 = +410%
6. **Exit Decision**: Profit target +60% triggered → Sell 50% of position

### **Example 3: Debit Spread**

**Scenario**: QQQ 628/629 Call Debit Spread

**Data Flow**:
1. **Entry**: Long leg 628c @ $0.19, Short leg 629c @ $0.12
   - Spread cost: $0.19 - $0.12 = $0.07
2. **Monitoring** (every 30 seconds):
   - Fetch quote for 628c: Mid $0.97
   - Fetch quote for 629c: Mid $0.85
   - Spread value: $0.97 - $0.85 = $0.12
3. **Position Value**: `current_value = $0.12`
4. **P&L**: ($0.12 - $0.07) / $0.07 = +71%

---

## ⚠️ **Important Notes**

### **Symbol List Management**

- **File Location**: `data/watchlist/0dte_list.csv`
- **Format**: CSV with columns: symbol, tier, category, notes
- **Tier Priority**: Tier 1 symbols processed first, then Tier 2
- **Order Within Tiers**: Maintains CSV order (as specified by user)
- **Scalability**: Add/remove symbols without code changes

### **Options Data Requirements**

- **Broker Account**: E*TRADE Options Trading account required
- **API Permissions**: Options trading permissions must be enabled
- **Rate Limits**: Standard E*TRADE API limits apply
- **Data Accuracy**: All data from broker ensures accuracy

### **Real-Time Price Tracking** (Rev 00238)

- **Frequency**: Every 30 seconds during position monitoring
- **Data Source**: E*TRADE Options API exclusively
- **Fallback**: If quote fetch fails, position value unchanged (retry next cycle)
- **Impact**: Critical for accurate exit decisions based on actual options P&L

---

## 🔗 **Related Documentation**

- **[README.md](./README.md)**: 0DTE Strategy overview
- **[Strategy.md](./Strategy.md)**: Detailed strategy documentation
- **[Alerts.md](./Alerts.md)**: Alert types and formats
- **ORB Strategy Data.md**: `../docs/Data.md` - ORB Strategy data architecture

---

## ✅ **Status**

**Current Version**: Rev 00326 (+ Rev 00238)  
**Deployment**: ✅ Active (Integrated with ORB Strategy)  
**Data Source**: E*TRADE API exclusively  
**Last Updated**: April 10, 2026

---

*Maintained by: Easy Trading Software Team*

