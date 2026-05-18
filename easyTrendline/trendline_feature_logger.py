#!/usr/bin/env python3
"""
Lightweight JSONL feature logger for trendline candidates/trades.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)


class TrendlineFeatureLogger:
    """Append-only JSONL logger with defensive file handling."""

    def __init__(self, base_dir: str = "data/trendline_optimizer") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.base_dir / "trendline_features.jsonl"

    def log_event(self, payload: Dict[str, Any]) -> None:
        """Write one normalized event row; failures are non-fatal."""
        row = dict(payload)
        row.setdefault("logged_at_utc", datetime.utcnow().isoformat())
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str))
                f.write("\n")
        except Exception as exc:
            log.warning("TRENDLINE_PIPELINE | stage=telemetry | action=write_failed | reason=%s", exc)

