"""
Resolve the canonical SO (Standard Order) signal collection list for a trading day.

Rev 00298: The merged 7:30 PT payload at
    daily_markers/signal_collection_730/YYYY-MM-DD.json
holds `pending_so_signals` — the authoritative list used for execution allowlists.
The Priority Optimizer file under priority_optimizer/daily_signals/ is derived from
the daily marker and can lag or hold a subset (e.g. only post-execution rows).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)


def resolve_so_signal_collection_for_date(
    date_str: str,
    gcs: Any,
    *,
    retrieved_data_dir: Optional[Path] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Return (payload, source_description).

    payload keys match priority_optimizer daily_signals JSON: signals, total_scanned, mode, metadata, date.

    Resolution order:
    1. daily_markers/signal_collection_730/{date}.json → pending_so_signals
    2. priority_optimizer/daily_signals/{date}_signals.json
    3. daily_markers/{date}.json → signals.signals
    """
    retrieved_data_dir = retrieved_data_dir or (
        Path(__file__).resolve().parent.parent / "priority_optimizer" / "retrieved_data"
    )

    def _read_gcs(path: str) -> Optional[Dict[str, Any]]:
        if not gcs or not getattr(gcs, "enabled", False):
            return None
        raw = gcs.read_string(path)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Invalid JSON from GCS path %s", path)
            return None

    def _read_local(name: str) -> Optional[Dict[str, Any]]:
        p = retrieved_data_dir / name
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Failed to read local %s: %s", p, exc)
            return None

    # 1) signal_collection_730 (authoritative merged SO list)
    path730 = f"daily_markers/signal_collection_730/{date_str}.json"
    data730 = _read_gcs(path730) or _read_local(f"signal_collection_730_{date_str}.json")
    if data730 and data730.get("date") == date_str:
        pending = data730.get("pending_so_signals") or []
        if pending:
            allow = data730.get("allowed_so_symbols") or []
            total_scanned = len(allow) if allow else len(pending)
            payload: Dict[str, Any] = {
                "date": date_str,
                "signals": pending,
                "total_scanned": total_scanned,
                "mode": "DEMO",
                "signal_count": len(pending),
                "metadata": {
                    "resolver": "signal_collection_730",
                    "gcs_path": path730,
                },
            }
            log.info(
                "SO signal list: using %s (%s symbols)",
                path730,
                len(pending),
            )
            return payload, "signal_collection_730"

    # 2) priority_optimizer daily_signals
    path_po = f"priority_optimizer/daily_signals/{date_str}_signals.json"
    po = _read_gcs(path_po) or _read_local(f"{date_str}_signals.json")
    if po and po.get("signals"):
        log.info(
            "SO signal list: using %s (%s symbols)",
            path_po,
            len(po["signals"]),
        )
        return po, "priority_optimizer/daily_signals"

    # 3) daily marker root
    path_dm = f"daily_markers/{date_str}.json"
    dm = _read_gcs(path_dm)
    if dm and dm.get("signals"):
        se = dm["signals"]
        sigs = se.get("signals") or []
        if sigs:
            log.info(
                "SO signal list: using %s (%s symbols)",
                path_dm,
                len(sigs),
            )
            return (
                {
                    "date": date_str,
                    "signals": sigs,
                    "total_scanned": se.get("total_scanned", 0),
                    "mode": se.get("mode", "DEMO"),
                    "signal_count": len(sigs),
                    "metadata": se.get("metadata") or {},
                },
                "daily_markers",
            )

    log.warning("No SO signal collection list found for %s", date_str)
    return None, ""
