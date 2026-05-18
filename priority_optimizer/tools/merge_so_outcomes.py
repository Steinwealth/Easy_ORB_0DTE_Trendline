#!/usr/bin/env python3
"""
Merge SO demo outcome ledger into comprehensive_data JSON/CSV for priority optimizer.

Reads:
  comprehensive_data/YYYY-MM-DD_comprehensive_data.json
  comprehensive_data/so_outcomes/YYYY-MM-DD_so_demo.json

Writes:
  comprehensive_data/YYYY-MM-DD_comprehensive_data_enriched.json
  comprehensive_data/YYYY-MM-DD_comprehensive_data_enriched.csv

Match key: symbol (uppercase). Optional trade_id stored as so_demo_trade_id.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="Trading date YYYY-MM-DD")
    args = ap.parse_args()
    date = args.date

    root = Path(__file__).resolve().parent.parent
    comp_path = root / "comprehensive_data" / f"{date}_comprehensive_data.json"
    out_path = root / "comprehensive_data" / f"{date}_so_demo.json"
    if not out_path.exists():
        out_path = root / "comprehensive_data" / "so_outcomes" / f"{date}_so_demo.json"
    if not comp_path.exists():
        raise SystemExit(f"Missing comprehensive file: {comp_path}")
    if not out_path.exists():
        raise SystemExit(f"Missing outcomes file: {out_path}")

    comp = json.loads(comp_path.read_text(encoding="utf-8"))
    records = comp.get("records") or comp
    if not isinstance(records, list):
        raise SystemExit("comprehensive JSON must have 'records' list")

    ledger = json.loads(out_path.read_text(encoding="utf-8"))
    by_sym: dict[str, dict] = {}
    for t in ledger.get("trades") or []:
        sym = str(t.get("symbol") or "").strip().upper()
        if sym:
            by_sym[sym] = t

    for rec in records:
        sym = str(rec.get("symbol") or "").strip().upper()
        t = by_sym.get(sym)
        if not t:
            rec["so_outcome_matched"] = False
            continue
        rec["so_outcome_matched"] = True
        rec["so_demo_trade_id"] = t.get("trade_id")
        rec["so_outcome_status"] = t.get("status")
        if t.get("status") == "closed":
            rec["so_exit_price"] = t.get("exit_price")
            rec["so_exit_quantity"] = t.get("quantity")
            rec["so_pnl_dollars"] = t.get("pnl_dollars")
            rec["so_pnl_pct"] = t.get("pnl_pct")
            rec["so_holding_minutes"] = t.get("holding_minutes")
            rec["so_exit_reason"] = t.get("exit_reason")
            rec["so_closed_by"] = t.get("closed_by")
            rec["so_win"] = bool((t.get("pnl_dollars") or 0) > 0)
        else:
            rec["so_open_context"] = t.get("open_context")
            if t.get("mark_price") is not None:
                rec["so_mark_price"] = t.get("mark_price")
            if t.get("mark_pnl_dollars") is not None:
                rec["so_mark_pnl_dollars"] = t.get("mark_pnl_dollars")
            if t.get("mark_pnl_pct") is not None:
                rec["so_mark_pnl_pct"] = t.get("mark_pnl_pct")

    enriched = {
        "date": comp.get("date", date),
        "total_records": len(records),
        "data_points_per_record": comp.get("data_points_per_record"),
        "so_outcomes_ledger": str(out_path.relative_to(root)),
        "records": records,
    }

    out_json = root / "comprehensive_data" / f"{date}_comprehensive_data_enriched.json"
    out_json.write_text(json.dumps(enriched, indent=2, default=str), encoding="utf-8")

    # CSV: union of keys from first record order + any extra keys from later rows (simple)
    keys: list[str] = []
    seen = set()
    for rec in records:
        for k in rec:
            if k not in seen:
                seen.add(k)
                keys.append(k)

    out_csv = root / "comprehensive_data" / f"{date}_comprehensive_data_enriched.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for rec in records:
            row = {k: rec.get(k) for k in keys}
            w.writerow(row)

    print(f"Wrote {out_json}")
    print(f"Wrote {out_csv}")


if __name__ == "__main__":
    main()
