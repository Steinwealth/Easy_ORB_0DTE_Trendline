#!/usr/bin/env python3
"""
Easy Collector - Profitable Pattern Analysis
Analyzes ORB -> SIGNAL -> OUTCOME patterns to find profitable trading signals
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
from collections import defaultdict
import numpy as np


class ProfitablePatternAnalyzer:
    """Analyze patterns from ORB -> SIGNAL -> OUTCOME to find profitable signals"""
    
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.snapshots = []
        self.patterns = []
        
    def load_snapshots(self, json_file: Optional[Path] = None):
        """Load snapshots from JSON file"""
        if json_file is None:
            # Find most recent snapshot file
            json_files = sorted(self.data_dir.glob("snapshots_*.json"), reverse=True)
            if not json_files:
                raise FileNotFoundError(f"No snapshot files found in {self.data_dir}")
            json_file = json_files[0]
        
        print(f"📂 Loading snapshots from {json_file.name}...")
        
        with open(json_file, 'r') as f:
            self.snapshots = json.load(f)
        
        print(f"✅ Loaded {len(self.snapshots)} snapshots")
        return len(self.snapshots)
    
    def build_patterns(self):
        """Build ORB -> SIGNAL -> OUTCOME patterns for each symbol/date"""
        print("\n🔗 Building ORB -> SIGNAL -> OUTCOME patterns...")
        
        # Group snapshots by date, market, and symbol
        by_key = defaultdict(lambda: {"ORB": None, "SIGNAL": None, "OUTCOME": None})
        
        for snapshot in self.snapshots:
            # Extract date
            timestamp = snapshot.get("timestamp_et") or snapshot.get("timestamp_utc")
            if not timestamp:
                continue
            
            if isinstance(timestamp, str):
                try:
                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    date_str = dt.strftime("%Y%m%d")
                except:
                    continue
            elif isinstance(timestamp, datetime):
                date_str = timestamp.strftime("%Y%m%d")
            else:
                continue
            
            market = snapshot.get("market", "UNKNOWN")
            symbol = snapshot.get("symbol", "UNKNOWN")
            snapshot_type = snapshot.get("snapshot_type", "UNKNOWN")
            
            key = f"{date_str}_{market}_{symbol}"
            
            if snapshot_type in ["ORB", "SIGNAL", "OUTCOME"]:
                by_key[key][snapshot_type] = snapshot
        
        # Build complete patterns
        self.patterns = []
        for key, snapshots in by_key.items():
            if snapshots["ORB"] and snapshots["SIGNAL"] and snapshots["OUTCOME"]:
                pattern = {
                    "key": key,
                    "date": key.split("_")[0],
                    "market": key.split("_")[1],
                    "symbol": key.split("_")[2],
                    "orb": snapshots["ORB"],
                    "signal": snapshots["SIGNAL"],
                    "outcome": snapshots["OUTCOME"]
                }
                self.patterns.append(pattern)
        
        print(f"✅ Built {len(self.patterns)} complete patterns (ORB+SIGNAL+OUTCOME)")
        return len(self.patterns)
    
    def calculate_returns(self):
        """Calculate returns from SIGNAL to OUTCOME"""
        print("\n📊 Calculating returns...")
        
        for pattern in self.patterns:
            signal = pattern["signal"]
            outcome = pattern["outcome"]
            
            # Get prices
            signal_price = None
            outcome_price = None
            
            # Try to get close price from signal
            signal_candle = signal.get("price_candle", {})
            if isinstance(signal_candle, dict):
                signal_price = signal_candle.get("close") or signal_candle.get("last_price")
            
            # Try to get close price from outcome
            outcome_candle = outcome.get("price_candle", {})
            if isinstance(outcome_candle, dict):
                outcome_price = outcome_candle.get("close") or outcome_candle.get("last_price")
            
            # Calculate return
            if signal_price and outcome_price and signal_price > 0:
                return_pct = ((outcome_price - signal_price) / signal_price) * 100
                pattern["return_pct"] = return_pct
                pattern["signal_price"] = signal_price
                pattern["outcome_price"] = outcome_price
            else:
                pattern["return_pct"] = None
                pattern["signal_price"] = signal_price
                pattern["outcome_price"] = outcome_price
            
            # Extract signal direction
            signal_data = signal.get("signal", {})
            if isinstance(signal_data, dict):
                pattern["signal_direction"] = signal_data.get("signal_direction")
                pattern["signal_score"] = signal_data.get("signal_score")
                pattern["tradable_flag"] = signal_data.get("tradable_flag")
                pattern["confidence"] = signal_data.get("confidence")
            else:
                pattern["signal_direction"] = None
                pattern["signal_score"] = None
                pattern["tradable_flag"] = None
                pattern["confidence"] = None
            
            # Extract ORB data
            orb = pattern["orb"]
            orb_block = orb.get("orb_block", {})
            if isinstance(orb_block, dict):
                pattern["orb_high"] = orb_block.get("orb_high")
                pattern["orb_low"] = orb_block.get("orb_low")
                pattern["orb_range_pct"] = orb_block.get("orb_range_pct")
                pattern["orb_break_state"] = orb_block.get("orb_break_state")
            
            # Extract indicators from signal
            signal_trend = signal.get("trend_momentum", {})
            if isinstance(signal_trend, dict):
                pattern["ema_trend"] = signal_trend.get("trend_direction")
                pattern["trend_strength"] = signal_trend.get("trend_strength_score")
            
            signal_vol = signal.get("volatility", {})
            if isinstance(signal_vol, dict):
                pattern["atr_pct"] = signal_vol.get("atr_pct")
                pattern["volatility_regime"] = signal_vol.get("volatility_regime")
            
            signal_osc = signal.get("oscillators", {})
            if isinstance(signal_osc, dict):
                pattern["rsi"] = signal_osc.get("rsi")
                pattern["rsi_slope"] = signal_osc.get("rsi_slope")
            
            signal_macd = signal.get("macd", {})
            if isinstance(signal_macd, dict):
                pattern["macd_histogram"] = signal_macd.get("macd_histogram")
                pattern["momentum_regime"] = signal_macd.get("momentum_regime")
        
        # Filter patterns with valid returns
        valid_patterns = [p for p in self.patterns if p.get("return_pct") is not None]
        print(f"✅ Calculated returns for {len(valid_patterns)} patterns")
        
        return len(valid_patterns)
    
    def analyze_profitable_signals(self):
        """Analyze which signal characteristics lead to profitable outcomes"""
        print("\n🎯 Analyzing profitable signal patterns...")
        
        valid_patterns = [p for p in self.patterns if p.get("return_pct") is not None]
        
        if not valid_patterns:
            print("⚠️  No valid patterns with returns")
            return {}
        
        # Define profitable threshold (e.g., >0.5% return)
        profitable_threshold = 0.5
        profitable = [p for p in valid_patterns if abs(p["return_pct"]) > profitable_threshold]
        unprofitable = [p for p in valid_patterns if abs(p["return_pct"]) <= profitable_threshold]
        
        analysis = {
            "total_patterns": len(valid_patterns),
            "profitable_count": len(profitable),
            "unprofitable_count": len(unprofitable),
            "profitable_pct": round((len(profitable) / len(valid_patterns)) * 100, 2),
            "avg_return_profitable": np.mean([abs(p["return_pct"]) for p in profitable]) if profitable else 0,
            "avg_return_unprofitable": np.mean([abs(p["return_pct"]) for p in unprofitable]) if unprofitable else 0,
            "insights": {}
        }
        
        # Analyze by signal direction
        long_profitable = [p for p in profitable if p.get("signal_direction") == "LONG"]
        short_profitable = [p for p in profitable if p.get("signal_direction") == "SHORT"]
        
        analysis["insights"]["by_direction"] = {
            "long_profitable": len(long_profitable),
            "short_profitable": len(short_profitable),
            "long_avg_return": np.mean([p["return_pct"] for p in long_profitable]) if long_profitable else 0,
            "short_avg_return": np.mean([abs(p["return_pct"]) for p in short_profitable]) if short_profitable else 0
        }
        
        # Analyze by signal score
        high_score = [p for p in profitable if p.get("signal_score", 0) > 0.7]
        medium_score = [p for p in profitable if 0.4 <= p.get("signal_score", 0) <= 0.7]
        low_score = [p for p in profitable if p.get("signal_score", 0) < 0.4]
        
        analysis["insights"]["by_signal_score"] = {
            "high_score_count": len(high_score),
            "medium_score_count": len(medium_score),
            "low_score_count": len(low_score),
            "high_score_avg_return": np.mean([abs(p["return_pct"]) for p in high_score]) if high_score else 0,
            "medium_score_avg_return": np.mean([abs(p["return_pct"]) for p in medium_score]) if medium_score else 0,
            "low_score_avg_return": np.mean([abs(p["return_pct"]) for p in low_score]) if low_score else 0
        }
        
        # Analyze by trend
        uptrend_profitable = [p for p in profitable if p.get("ema_trend") == "UP"]
        downtrend_profitable = [p for p in profitable if p.get("ema_trend") == "DOWN"]
        
        analysis["insights"]["by_trend"] = {
            "uptrend_profitable": len(uptrend_profitable),
            "downtrend_profitable": len(downtrend_profitable),
            "uptrend_avg_return": np.mean([p["return_pct"] for p in uptrend_profitable]) if uptrend_profitable else 0,
            "downtrend_avg_return": np.mean([abs(p["return_pct"]) for p in downtrend_profitable]) if downtrend_profitable else 0
        }
        
        # Analyze by volatility regime
        high_vol_profitable = [p for p in profitable if p.get("volatility_regime") in ["HIGH", "EXTREME"]]
        normal_vol_profitable = [p for p in profitable if p.get("volatility_regime") == "NORMAL"]
        
        analysis["insights"]["by_volatility"] = {
            "high_vol_profitable": len(high_vol_profitable),
            "normal_vol_profitable": len(normal_vol_profitable),
            "high_vol_avg_return": np.mean([abs(p["return_pct"]) for p in high_vol_profitable]) if high_vol_profitable else 0,
            "normal_vol_avg_return": np.mean([abs(p["return_pct"]) for p in normal_vol_profitable]) if normal_vol_profitable else 0
        }
        
        # Analyze by ORB break state
        orb_breakout_up = [p for p in profitable if p.get("orb_break_state") == "BREAKOUT_UP"]
        orb_breakout_down = [p for p in profitable if p.get("orb_break_state") == "BREAKOUT_DOWN"]
        orb_in_range = [p for p in profitable if p.get("orb_break_state") == "IN_RANGE"]
        
        analysis["insights"]["by_orb_break"] = {
            "breakout_up_profitable": len(orb_breakout_up),
            "breakout_down_profitable": len(orb_breakout_down),
            "in_range_profitable": len(orb_in_range),
            "breakout_up_avg_return": np.mean([p["return_pct"] for p in orb_breakout_up]) if orb_breakout_up else 0,
            "breakout_down_avg_return": np.mean([abs(p["return_pct"]) for p in orb_breakout_down]) if orb_breakout_down else 0,
            "in_range_avg_return": np.mean([abs(p["return_pct"]) for p in orb_in_range]) if orb_in_range else 0
        }
        
        # Analyze by RSI
        rsi_oversold = [p for p in profitable if p.get("rsi") and p["rsi"] < 30]
        rsi_overbought = [p for p in profitable if p.get("rsi") and p["rsi"] > 70]
        rsi_neutral = [p for p in profitable if p.get("rsi") and 30 <= p["rsi"] <= 70]
        
        analysis["insights"]["by_rsi"] = {
            "oversold_profitable": len(rsi_oversold),
            "overbought_profitable": len(rsi_overbought),
            "neutral_profitable": len(rsi_neutral),
            "oversold_avg_return": np.mean([abs(p["return_pct"]) for p in rsi_oversold]) if rsi_oversold else 0,
            "overbought_avg_return": np.mean([abs(p["return_pct"]) for p in rsi_overbought]) if rsi_overbought else 0,
            "neutral_avg_return": np.mean([abs(p["return_pct"]) for p in rsi_neutral]) if rsi_neutral else 0
        }
        
        return analysis
    
    def generate_recommendations(self, analysis: Dict) -> List[str]:
        """Generate trading recommendations based on analysis"""
        recommendations = []
        
        insights = analysis.get("insights", {})
        
        # Signal score recommendations
        by_score = insights.get("by_signal_score", {})
        if by_score.get("high_score_avg_return", 0) > by_score.get("low_score_avg_return", 0):
            recommendations.append(
                f"✅ Focus on high signal scores (>0.7): "
                f"{by_score['high_score_count']} profitable trades, "
                f"avg return {by_score['high_score_avg_return']:.2f}%"
            )
        
        # Trend recommendations
        by_trend = insights.get("by_trend", {})
        if by_trend.get("uptrend_avg_return", 0) > 0:
            recommendations.append(
                f"✅ Uptrend signals profitable: "
                f"{by_trend['uptrend_profitable']} trades, "
                f"avg return {by_trend['uptrend_avg_return']:.2f}%"
            )
        if by_trend.get("downtrend_avg_return", 0) > 0:
            recommendations.append(
                f"✅ Downtrend signals profitable: "
                f"{by_trend['downtrend_profitable']} trades, "
                f"avg return {by_trend['downtrend_avg_return']:.2f}%"
            )
        
        # ORB break recommendations
        by_orb = insights.get("by_orb_break", {})
        if by_orb.get("breakout_up_avg_return", 0) > by_orb.get("in_range_avg_return", 0):
            recommendations.append(
                f"✅ ORB Breakout UP signals: "
                f"{by_orb['breakout_up_profitable']} profitable trades, "
                f"avg return {by_orb['breakout_up_avg_return']:.2f}%"
            )
        if by_orb.get("breakout_down_avg_return", 0) > by_orb.get("in_range_avg_return", 0):
            recommendations.append(
                f"✅ ORB Breakout DOWN signals: "
                f"{by_orb['breakout_down_profitable']} profitable trades, "
                f"avg return {by_orb['breakout_down_avg_return']:.2f}%"
            )
        
        # Volatility recommendations
        by_vol = insights.get("by_volatility", {})
        if by_vol.get("high_vol_avg_return", 0) > by_vol.get("normal_vol_avg_return", 0):
            recommendations.append(
                f"✅ High volatility regimes: "
                f"{by_vol['high_vol_profitable']} profitable trades, "
                f"avg return {by_vol['high_vol_avg_return']:.2f}%"
            )
        
        return recommendations
    
    def save_analysis(self, analysis: Dict, output_file: Path):
        """Save analysis results"""
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w') as f:
            json.dump(analysis, f, indent=2, default=str)
        
        print(f"✅ Analysis saved to {output_file.name}")
    
    def print_report(self, analysis: Dict, recommendations: List[str]):
        """Print analysis report"""
        print("\n" + "=" * 80)
        print("PROFITABLE PATTERN ANALYSIS REPORT")
        print("=" * 80)
        
        print(f"\n📊 SUMMARY")
        print(f"  Total Patterns: {analysis['total_patterns']}")
        print(f"  Profitable: {analysis['profitable_count']} ({analysis['profitable_pct']}%)")
        print(f"  Unprofitable: {analysis['unprofitable_count']}")
        print(f"  Avg Return (Profitable): {analysis['avg_return_profitable']:.2f}%")
        print(f"  Avg Return (Unprofitable): {analysis['avg_return_unprofitable']:.2f}%")
        
        insights = analysis.get("insights", {})
        
        if "by_signal_score" in insights:
            print(f"\n🎯 BY SIGNAL SCORE")
            by_score = insights["by_signal_score"]
            print(f"  High Score (>0.7): {by_score['high_score_count']} trades, avg return {by_score['high_score_avg_return']:.2f}%")
            print(f"  Medium Score (0.4-0.7): {by_score['medium_score_count']} trades, avg return {by_score['medium_score_avg_return']:.2f}%")
            print(f"  Low Score (<0.4): {by_score['low_score_count']} trades, avg return {by_score['low_score_avg_return']:.2f}%")
        
        if "by_trend" in insights:
            print(f"\n📈 BY TREND")
            by_trend = insights["by_trend"]
            print(f"  Uptrend: {by_trend['uptrend_profitable']} trades, avg return {by_trend['uptrend_avg_return']:.2f}%")
            print(f"  Downtrend: {by_trend['downtrend_profitable']} trades, avg return {by_trend['downtrend_avg_return']:.2f}%")
        
        if "by_orb_break" in insights:
            print(f"\n🚀 BY ORB BREAK STATE")
            by_orb = insights["by_orb_break"]
            print(f"  Breakout UP: {by_orb['breakout_up_profitable']} trades, avg return {by_orb['breakout_up_avg_return']:.2f}%")
            print(f"  Breakout DOWN: {by_orb['breakout_down_profitable']} trades, avg return {by_orb['breakout_down_avg_return']:.2f}%")
            print(f"  In Range: {by_orb['in_range_profitable']} trades, avg return {by_orb['in_range_avg_return']:.2f}%")
        
        if recommendations:
            print(f"\n💡 RECOMMENDATIONS")
            for rec in recommendations:
                print(f"  {rec}")
        
        print("\n" + "=" * 80)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Analyze profitable trading patterns")
    parser.add_argument("--data-dir", type=str, default="data/easy_collector/firestore_downloads", help="Data directory")
    parser.add_argument("--json-file", type=str, help="Specific JSON file to analyze")
    parser.add_argument("--output", type=str, help="Output file for analysis")
    
    args = parser.parse_args()
    
    data_dir = Path(args.data_dir)
    json_file = Path(args.json_file) if args.json_file else None
    
    analyzer = ProfitablePatternAnalyzer(data_dir)
    
    try:
        # Load data
        analyzer.load_snapshots(json_file)
        
        # Build patterns
        analyzer.build_patterns()
        
        # Calculate returns
        analyzer.calculate_returns()
        
        # Analyze
        analysis = analyzer.analyze_profitable_signals()
        
        # Generate recommendations
        recommendations = analyzer.generate_recommendations(analysis)
        
        # Print report
        analyzer.print_report(analysis, recommendations)
        
        # Save analysis
        if args.output:
            analyzer.save_analysis(analysis, Path(args.output))
        else:
            output_file = data_dir / f"pattern_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            analyzer.save_analysis(analysis, output_file)
        
        return 0
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
