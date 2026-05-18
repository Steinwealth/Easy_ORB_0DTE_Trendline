#!/usr/bin/env python3
"""
Execution-only daily dataset writer for Trendline 0DTE trades.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .trendline_models import OHLCVBar


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return _to_utc(dt).isoformat()


@dataclass
class TrendlineExecutedTradeDataset:
    """Stores and updates execution-only rows keyed by trade_id."""

    base_dir: str = "data/trendline_optimizer"
    _rows: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _loaded_date: Optional[str] = None

    def _date_key(self, now: Optional[datetime] = None) -> str:
        ts = _to_utc(now or datetime.now(timezone.utc))
        return ts.strftime("%Y-%m-%d")

    def _file_path(self, date_key: str) -> Path:
        p = Path(self.base_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p / f"trendline_executed_trades_{date_key}.json"

    def _ensure_loaded(self, date_key: str) -> None:
        if self._loaded_date == date_key:
            return
        self._rows = {}
        self._loaded_date = date_key
        fp = self._file_path(date_key)
        if not fp.exists():
            return
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
            for row in payload.get("trades", []) if isinstance(payload, dict) else []:
                tid = str(row.get("trade_id") or "").strip()
                if tid:
                    self._rows[tid] = dict(row)
        except Exception:
            self._rows = {}

    def _save(self, date_key: str) -> None:
        fp = self._file_path(date_key)
        rows = sorted(self._rows.values(), key=lambda r: (str(r.get("execution_timestamp") or ""), str(r.get("trade_id") or "")))
        pnl_vals = [float(r["final_pnl_pct"]) for r in rows if isinstance(r.get("final_pnl_pct"), (int, float))]
        max_vals = [float(r["max_pnl_pct"]) for r in rows if isinstance(r.get("max_pnl_pct"), (int, float))]
        summary = {
            "total_trades": len(rows),
            "win_rate": (sum(1 for v in pnl_vals if v > 0.0) / len(pnl_vals)) if pnl_vals else None,
            "avg_pnl": (sum(pnl_vals) / len(pnl_vals)) if pnl_vals else None,
            "avg_max_pnl": (sum(max_vals) / len(max_vals)) if max_vals else None,
        }
        payload = {
            "date": date_key,
            "summary": summary,
            "trades": rows,
        }
        tmp = fp.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(fp)

    @staticmethod
    def _nearest_bar_idx(bars: Sequence[OHLCVBar], ts: datetime) -> Optional[int]:
        if not bars:
            return None
        target = _to_utc(ts)
        best_idx = None
        best_dist = None
        for idx, b in enumerate(bars):
            bts = _to_utc(b.ts)
            dist = abs((bts - target).total_seconds())
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_idx = idx
        return best_idx

    @staticmethod
    def _atr_from_bars(bars: Sequence[OHLCVBar], idx: int, period: int = 14) -> Optional[float]:
        if idx < 1:
            return None
        start = max(1, idx - period + 1)
        trs: List[float] = []
        for i in range(start, idx + 1):
            cur = bars[i]
            prev = bars[i - 1]
            h = float(cur.high)
            l = float(cur.low)
            pc = float(prev.close)
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        if not trs:
            return None
        return float(sum(trs) / len(trs))

    @staticmethod
    def _trend_direction(bars: Sequence[OHLCVBar]) -> Optional[str]:
        if len(bars) < 2:
            return None
        start = float(bars[0].open)
        end = float(bars[-1].close)
        if start <= 0:
            return None
        pct = (end - start) / start
        if pct > 0.001:
            return "uptrend"
        if pct < -0.001:
            return "downtrend"
        return "sideways"

    @staticmethod
    def _entry_quality_label(max_pnl_pct: Optional[float]) -> Optional[str]:
        if not isinstance(max_pnl_pct, (int, float)):
            return None
        v = float(max_pnl_pct)
        if v >= 20.0:
            return "A"
        if v >= 10.0:
            return "B"
        if v >= -5.0:
            return "C"
        return "D"

    def record_execution(
        self,
        *,
        trade_id: str,
        symbol: str,
        option_side: Optional[str],
        execution_ts: datetime,
        signal_ts: Optional[datetime],
        break_distance: Optional[float],
        bars_5m: Sequence[OHLCVBar],
        distance_increasing: Optional[bool] = None,
    ) -> None:
        tid = str(trade_id or "").strip()
        if not tid:
            return
        date_key = self._date_key(execution_ts)
        self._ensure_loaded(date_key)

        signal_time = _to_utc(signal_ts or execution_ts)
        bar_idx = self._nearest_bar_idx(bars_5m, signal_time)
        if bar_idx is None:
            return
        b = bars_5m[bar_idx]
        o, h, l, c = float(b.open), float(b.high), float(b.low), float(b.close)
        rng = max(0.0, h - l)
        body = abs(c - o)
        upper = max(0.0, h - max(o, c))
        lower = max(0.0, min(o, c) - l)
        atr = self._atr_from_bars(bars_5m, bar_idx)

        prior5 = list(bars_5m[max(0, bar_idx - 5):bar_idx])
        prior_lows = [float(x.low) for x in prior5]
        prior_highs = [float(x.high) for x in prior5]
        prior_ranges = [float(x.high) - float(x.low) for x in prior5]
        higher_low = any(prior_lows[i] > prior_lows[i - 1] for i in range(1, len(prior_lows))) if len(prior_lows) >= 2 else None
        lower_high = any(prior_highs[i] < prior_highs[i - 1] for i in range(1, len(prior_highs))) if len(prior_highs) >= 2 else None
        prior_dir = self._trend_direction(prior5)
        avg_range_last_5 = (sum(prior_ranges) / len(prior_ranges)) if prior_ranges else None

        nxt = list(bars_5m[bar_idx + 1: bar_idx + 4])
        next_closes = [float(x.close) for x in nxt]
        continuation_distance = (next_closes[-1] - c) if next_closes else None
        did_expand = None
        if nxt:
            fut_h = max(float(x.high) for x in nxt)
            fut_l = min(float(x.low) for x in nxt)
            did_expand = bool((fut_h - fut_l) > rng) if rng > 0 else None

        row = self._rows.get(tid, {})
        entry_snapshot = row.get("entry_snapshot")
        if not isinstance(entry_snapshot, dict):
            entry_snapshot = {
                "break_distance": break_distance,
                "break_distance_atr": (break_distance / atr) if (atr and isinstance(break_distance, (int, float))) else None,
                "body_ratio": (body / rng) if rng > 0 else None,
                "candle_range": rng,
                "upper_wick_ratio": (upper / rng) if rng > 0 else None,
                "lower_wick_ratio": (lower / rng) if rng > 0 else None,
                "prior_trend_direction": prior_dir,
                "higher_low_present": higher_low,
                "lower_high_present": lower_high,
                "avg_range_last_5": avg_range_last_5,
                "distance_increasing": distance_increasing,
            }
        row.update(
            {
                "trade_id": tid,
                "symbol": symbol,
                "option_side": (option_side or "").lower() if option_side else None,
                "executed": True,
                "execution_timestamp": _iso(execution_ts),
                "signal_timestamp": _iso(signal_time),
                "signal_candle_time": _iso(_to_utc(b.ts)),
                "break_distance": break_distance,
                "break_distance_atr": (break_distance / atr) if (atr and isinstance(break_distance, (int, float))) else None,
                "body_ratio": (body / rng) if rng > 0 else None,
                "candle_range": rng,
                "upper_wick_ratio": (upper / rng) if rng > 0 else None,
                "lower_wick_ratio": (lower / rng) if rng > 0 else None,
                "prior_trend_direction": prior_dir,
                "higher_low_present": higher_low,
                "lower_high_present": lower_high,
                "avg_range_last_5": avg_range_last_5,
                "distance_increasing": distance_increasing,
                "entry_snapshot": entry_snapshot,
                "next_1_candle_close": next_closes[0] if len(next_closes) >= 1 else None,
                "next_2_candle_close": next_closes[1] if len(next_closes) >= 2 else None,
                "next_3_candle_close": next_closes[2] if len(next_closes) >= 3 else None,
                "continuation_distance": continuation_distance,
                "did_price_expand": did_expand,
                "pnl_after_1_min": row.get("pnl_after_1_min"),
                "pnl_after_3_min": row.get("pnl_after_3_min"),
                "pnl_after_5_min": row.get("pnl_after_5_min"),
                "max_pnl_pct": row.get("max_pnl_pct"),
                "final_pnl_pct": row.get("final_pnl_pct"),
                "entry_quality_label": row.get("entry_quality_label"),
            }
        )
        self._rows[tid] = row
        self._save(date_key)

    def update_pnl_snapshot(
        self,
        *,
        trade_id: str,
        execution_ts: Optional[datetime],
        held_minutes: Optional[float],
        pnl_pct: Optional[float],
    ) -> None:
        tid = str(trade_id or "").strip()
        if not tid:
            return
        date_key = self._date_key(execution_ts)
        self._ensure_loaded(date_key)
        row = self._rows.get(tid)
        if not row:
            return
        if isinstance(pnl_pct, (int, float)):
            cur_max = row.get("max_pnl_pct")
            if not isinstance(cur_max, (int, float)) or pnl_pct > cur_max:
                row["max_pnl_pct"] = float(pnl_pct)
            if isinstance(held_minutes, (int, float)):
                if held_minutes >= 1.0 and row.get("pnl_after_1_min") is None:
                    row["pnl_after_1_min"] = float(pnl_pct)
                if held_minutes >= 3.0 and row.get("pnl_after_3_min") is None:
                    row["pnl_after_3_min"] = float(pnl_pct)
                if held_minutes >= 5.0 and row.get("pnl_after_5_min") is None:
                    row["pnl_after_5_min"] = float(pnl_pct)
            row["entry_quality_label"] = self._entry_quality_label(row.get("max_pnl_pct"))
        self._rows[tid] = row
        self._save(date_key)

    def update_final_outcome(
        self,
        *,
        trade_id: str,
        execution_ts: Optional[datetime],
        final_pnl_pct: Optional[float],
    ) -> None:
        tid = str(trade_id or "").strip()
        if not tid:
            return
        date_key = self._date_key(execution_ts)
        self._ensure_loaded(date_key)
        row = self._rows.get(tid)
        if not row:
            return
        if isinstance(final_pnl_pct, (int, float)):
            row["final_pnl_pct"] = float(final_pnl_pct)
            cur_max = row.get("max_pnl_pct")
            if not isinstance(cur_max, (int, float)) or float(final_pnl_pct) > float(cur_max):
                row["max_pnl_pct"] = float(final_pnl_pct)
        row["entry_quality_label"] = self._entry_quality_label(row.get("max_pnl_pct"))
        self._rows[tid] = row
        self._save(date_key)
