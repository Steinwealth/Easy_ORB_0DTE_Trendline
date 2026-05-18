# Easy 0DTE Strategy - Alert Types & Formats

**Last Updated**: January 9, 2026  
**Version**: 2.38.0 (Rev 00238)  
**Status**: ✅ **PRODUCTION ACTIVE**

---

## 📋 **Alert Overview**

The Easy 0DTE Strategy sends alerts via Telegram for all key events in the options trading workflow. Alerts are integrated with the ORB Strategy alert system and provide real-time notifications for signal collection, execution, exits, and performance.

---

## 🔔 **Alert Types**

### **1. Signal Collection Alert** (Unified - SO + 0DTE) ⭐ **FINAL CONFIRMED LISTS**

**Status**: ✅ **Integrated into Unified Signal Collection Alert** (Rev 00238)

**Purpose**: **Single alert** showing both final confirmed trade lists (after all rules and risk management)

**Trigger**: After signal collection and rules confirmation completes (7:30 AM PT)

**Source**: `modules/prime_alert_manager.py` → `send_signal_collection_alert()`

**Content**:

**SO Signal Collection** (ORB Strategy):
- Final confirmed SO trades ready for execution (after all rules and risk management)
- Number of confirmed SO trades (typically 6-15)
- All rules and risk management applied:
  - 3 strict validation rules (price, volume color, previous candle)
  - Red Day Filter (Portfolio-Level)
  - Signal-Level Filtering
  - Position sizing (rank-based multipliers)
  - Capital allocation (90% allocation)
  - Position limits (max 15 concurrent)
- Final execution-ready list

**0DTE Signal Collection** (0DTE Strategy):
- Final confirmed 0DTE options trades ready for execution (after all rules and risk management)
- Number of confirmed 0DTE options trades (qualified)
- All rules and risk management applied:
  - Convex Eligibility Filter (score ≥ 0.75, 8 criteria)
  - Strategy selection (long call, debit spread, momentum scalper, ITM probability)
  - Strike selection (delta, premium, liquidity validation)
  - Hard gate validation (open interest ≥ 100, bid/ask spread ≤ 5%, volume ≥ 50)
  - Position size validation (capital allocation, max position limits)
  - Red Day check (portfolio-level protection)
  - Risk management (position limits, capital allocation, liquidity requirements)
- Qualified signal details:
  - Symbol and option type (CALL/PUT)
  - Eligibility score (Convex Eligibility Filter score ≥ 0.75)
  - Strategy type (long call, debit spread, etc.)
  - Delta (target delta achieved)
  - Strike details (long strike, short strike for spreads)
  - Premium range ($0.15-$0.60)
- Final execution-ready list

**Key Points**:
- Both strategies confirm rules **after ORB Capture** and **before execution**
- Signal Collection alert contains **final confirmed trade lists** ready for execution (after all rules and risk management)
- All rules and risk management applied during Signal Collection window (7:15-7:30 AM PT)
- Both lists represent **final execution-ready trades** confirmed to open positions
- Execution alerts sent **after** trades are executed (separate for ORB SO and 0DTE Options)

**Format**: Part of unified Signal Collection alert (single alert for both SO and 0DTE)

---

### **2. Options Execution Alert** ⭐ **SEPARATE EXECUTION ALERT**

**Trigger**: After 0DTE options trades are executed (7:30 AM PT)

**Alert Method**: `send_options_execution_alert()`

**Purpose**: **Separate alert** showing executed 0DTE options trades from **0DTE Signal Collection** (final confirmed list)

**Note**: Signal Collection alert (sent before execution) contains final confirmed trade lists ready for execution. This execution alert is sent **after** trades are executed.

**Information Included**:
- **0DTE Summary**:
  - 0DTE Symbols monitored (111 from 0dte_list.csv)
  - 0DTE Options found (qualified)
  - Filtered (rejected) signals (failed rules or risk management)
  - Failed executions (execution errors, if any)
  - Average momentum score

- **Executed Trades** (for each position from 0DTE Signal Collection):
  - Position ID (shortened format: `DEMO_SYMBOL_YYMMDD_LONG_SHORT_TYPE_microseconds`)
  - Symbol and option type (CALL/PUT)
  - Strategy type (Debit Spread, Long Call/Put, ITM Prob, Lotto, etc.)
  - Priority rank and priority score
  - Momentum score (0-100)
  - Delta (long leg delta)
  - Strike details (long strike @ price, short strike @ price)
  - Net debit/credit
  - Max profit
  - Capital allocation (% of account)
  - Trade ID

- **Capital Deployment**:
  - Total capital deployed
  - Account balance
  - Deployment percentage

- **Monitoring**: All positions tracked (every 30 seconds)

**Alert Format**:
```
🔮 Easy ORB 0DTE — Options Execution | DEMO Mode
          Time: 7:30 AM PT (10:30 AM ET)

🎙️ 0DTE Summary (7:15 AM PT):
          • 0DTE Symbols: 38
          • 0DTE Options Found: 5
          • Filtered (Expensive): 2
          • Failed Executions: 0
          • Avg Momentum: 75/100

💼 0DTE Options Executed: 3

1) 🟢 1 • QQQ CALL Debit Spread
          Rank #1 • Priority Score 0.875
          75% Confidence • 80/100 Momentum
          0.25 Delta
          Long: QQQ 260107C585 @ $2.50
          Short: QQQ 260107C590 @ $1.20
          Net Debit: $1.30 • Max Profit: $3.70
          35.0% Of Account
          Trade ID: DEMO_QQQ_260107_585_590_c_546

💰 Capital Deployment:
          • Deployed: $1,950.00 / $5,000.00 (39.0%)

🛡️ Monitoring: All positions tracked (every 30 seconds)
```

---

### **3. Options Position Exit Alert** (Individual)

**Trigger**: When a single options position is closed

**Alert Method**: `send_options_position_exit_alert()`

**Exit Reasons**:
- `hard_stop`: Hard stop triggered (-45% for debit spreads)
- `invalidation_stop`: Structural stop (VWAP/ORB reclaim)
- `time_stop`: Time stop triggered (no favorable move)
- `fail_safe`: Fail-safe exit (-60% absolute stop)
- `profit_target`: Profit target hit (+60% or +120%)
- `runner_target`: Runner exit (VWAP/ORB reclaim)
- `eod_close`: End of day close
- `health_emergency`: Health emergency exit

**Information Included**:
- P&L percentage and dollar amount
- Position type (Debit Spread, Lotto)
- Entry price and exit price
- Exit reason
- Holding time
- Trade ID

**Alert Format**:
```
🔮 💠 OPTION CLOSED | DEMO Mode
          Time: 8:15 AM PT (11:15 AM ET)

1) 💰 +45.23% +$234.50
          QQQ CALL Debit Spread
          Entry: $1.30 • Exit: $1.89
          Reason: Profit Target Hit
          
          Holding Time: 45m
          Trade ID: DEMO_QQQ_260107_585_590_c_546

📊 Position closed by Options Exit Manager
```

---

### **4. Options Aggregated Exit Alert**

**Trigger**: When multiple positions are closed simultaneously (EOD, emergency exits, batch exits)

**Alert Method**: `send_options_aggregated_exit_alert()`

**Common Scenarios**:
- End of day close (all remaining positions)
- Health emergency exit (all positions closed)
- Fail-safe batch exit

**Information Included**:
- Total P&L (sum of all positions)
- Individual position details (same as individual exit alert)
- Common exit reason

**Alert Format**:
```
🔮 💠 OPTIONS CLOSED | DEMO Mode
          Time: 12:55 PM PT (3:55 PM ET)

💰 Total P&L: +$456.78

1) 💰 +45.23% +$234.50
          QQQ CALL Debit Spread
          Entry: $1.30 • Exit: $1.89
          Reason: eod_close
          
          Holding Time: 5h 25m
          Trade ID: DEMO_QQQ_260107_585_590_c_546

2) 📉 -12.50% -$78.90
          SPY PUT Debit Spread
          Entry: $2.10 • Exit: $1.84
          Reason: eod_close
          
          Holding Time: 5h 15m
          Trade ID: DEMO_SPY_260107_580_575_p_234

📊 Positions closed by Options Exit Manager
```

---

### **5. Options Partial Profit Alert**

**Trigger**: When automated profit targets are hit (+60% sell 50%, +120% sell 25%)

**Alert Method**: `send_options_partial_profit_alert()`

**Targets**:
- **First Target**: +60% → Sell 50% of position
- **Second Target**: +120% → Sell 25% of remaining position

**Information Included**:
- Target name (First Target or Second Target)
- Symbol and position type
- Partial quantity sold
- Remaining quantity (runner)
- Profit locked

**Alert Format**:
```
🔮 💠 FIRST TARGET EXIT | DEMO Mode
          Time: 8:30 AM PT (11:30 AM ET)

1) 💰 First Target (+60%)
          QQQ Options Position
          • Sold 50%: 1 contracts
          • Remaining: 1 contracts (runner)
          • Profit Locked: +$78.00
          
          Trade ID: DEMO_QQQ_260107_585_590_c_546

📊 Automated exit system profit capture
```

---

### **6. Options Runner Exit Alert**

**Trigger**: When runner position exits (VWAP/ORB reclaim or time cutoff)

**Alert Method**: `send_options_runner_exit_alert()`

**Exit Conditions**:
- VWAP reclaim (price reclaims VWAP against position)
- ORB midpoint reclaim (price reclaims ORB midpoint)
- Time cutoff (near EOD)

**Information Included**:
- P&L percentage and dollar amount
- Symbol and position type (Runner)
- Exit price
- Exit reason (VWAP/ORB reclaim details)
- Trade ID

**Alert Format**:
```
🔮 💠 RUNNER EXIT | DEMO Mode
          Time: 10:45 AM PT (1:45 PM ET)

1) 💰 +125.50% +$163.15
          QQQ Options Runner
          Exit Price: $2.93
          Reason: VWAP reclaim - Price $385.20 < VWAP $386.50
          
          Trade ID: DEMO_QQQ_260107_585_590_c_546

📊 Automated exit system profit capture
```

---

### **7. Options Health Check Alert**

**Trigger**: When portfolio health check is performed (every 15 minutes)

**Alert Method**: `send_options_health_check_alert()`

**Health Statuses**:
- `EMERGENCY`: Critical issues detected, positions closed
- `WARNING`: Warning flags detected, weak positions closed
- `OK`: No issues detected, normal operations

**Information Included**:
- Health status
- Red flags (if any)
- Action taken (positions closed, etc.)

**Alert Format**:
```
🚨 OPTIONS PORTFOLIO HEALTH - EMERGENCY | DEMO Mode
          Time: 9:30 AM PT (12:30 PM ET)

📊 Red Flags:
   ❌ Portfolio P&L < -10%
   ❌ Multiple positions hitting hard stops
   ❌ Market conditions deteriorating

🛡️ Action: Closed 3 options positions
💰 Exited early to preserve capital

🛡️ Health check system protecting capital
```

---

### **8. Options End-of-Day Report**

**Trigger**: At end of trading day (12:55 PM PT)

**Alert Method**: `send_options_end_of_day_report()`

**Information Included**:
- **Daily Stats**:
  - Positions opened
  - Positions closed
  - Total P&L
  - Winning trades / Losing trades
  - Win rate
  - Best trade / Worst trade
  - Average P&L per trade

- **Weekly Stats**:
  - Weekly P&L
  - Weekly win rate
  - Weekly best/worst trade

- **All-Time Stats** (if available):
  - Total P&L
  - Total trades
  - All-time win rate

- **Account Performance**:
  - Starting balance
  - Ending balance
  - Daily return %
  - Weekly return %
  - All-time return %

**Alert Format**:
```
🔮 💠 0DTE END-OF-DAY REPORT | DEMO Mode
          Date: January 7, 2026
          Time: 12:55 PM PT (3:55 PM ET)

📊 Daily Performance:
          • Positions Opened: 5
          • Positions Closed: 5
          • Total P&L: +$456.78
          • Winning Trades: 3
          • Losing Trades: 2
          • Win Rate: 60.0%
          • Best Trade: +$234.50 (+45.23%)
          • Worst Trade: -$78.90 (-12.50%)
          • Avg P&L: +$91.36

📈 Weekly Performance:
          • Weekly P&L: +$1,234.56
          • Weekly Win Rate: 65.0%
          • Weekly Best: +$456.78
          • Weekly Worst: -$123.45

💰 Account Performance:
          • Starting Balance: $5,000.00
          • Ending Balance: $5,456.78
          • Daily Return: +9.14%
          • Weekly Return: +24.69%

📊 0DTE Strategy performance summary
```

---

## 🔗 **Alert Integration**

### **Integration with ORB Strategy**

- **Signal Collection**: Included in **unified Signal Collection alert** (single alert for both SO and 0DTE final confirmed lists)
- **Execution**: Separate Options Execution alerts
- **Exits**: Individual and aggregated exit alerts
- **EOD**: Separate Options EOD report

### **Alert Timing**

- **Signal Collection**: 7:30 AM PT (unified alert with both SO and 0DTE final confirmed lists)
- **Execution**: 7:30 AM PT (after trade execution)
- **Exits**: Throughout day (as positions close)
- **Partial Profits**: Throughout day (as targets hit)
- **Runner Exits**: Throughout day (as runners exit)
- **Health Checks**: Every 15 minutes (if issues detected)
- **EOD Report**: 12:55 PM PT (end of trading day)

---

## 📱 **Alert Delivery**

All alerts are delivered via **Telegram** using the `PrimeAlertManager` system.

**Alert Levels**:
- `SUCCESS`: Green alerts (executions, profits)
- `WARNING`: Yellow alerts (exits, health warnings)
- `ERROR`: Red alerts (failures, emergencies)
- `INFO`: Blue alerts (status updates)

---

## 🔍 **Alert Formatting**

### **Position ID Format** (Rev 00232)

**Shortened Format**: `{MODE}_{SYMBOL}_{YYMMDD}_{LONG}_{SHORT}_{TYPE}_{MICROSECONDS}`

**Examples**:
- Debit Spread: `DEMO_QQQ_260107_585_590_c_546`
- Lotto: `DEMO_SPY_260107_580_p_234`
- Live Mode: `LIVE_SPX_260107_5850_5860_c_789`

### **Time Format**

- **PT Time**: Pacific Time (e.g., "7:30 AM PT")
- **ET Time**: Eastern Time (e.g., "10:30 AM ET")
- Both times shown in all alerts

---

## 📊 **Alert Examples**

See individual alert sections above for complete format examples.

---

*Last Updated: January 7, 2026*  
*Version: 2.31.0 (Rev 00233)*  
*Maintained by: Easy Trading Software Team*

