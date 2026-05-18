"""
Easy ORB SO — centralized observability wrapper.

Emits telemetry to the shared Observability PostgreSQL store via ObservabilityClient.
All calls are non-fatal: failures are logged and never propagate to trading logic.
"""

from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_STRAT_ROOT = Path(__file__).resolve().parent
if str(_STRAT_ROOT) not in sys.path:
    sys.path.insert(0, str(_STRAT_ROOT))

_OBS_ROOT = _STRAT_ROOT.parent / "Observability"
if _OBS_ROOT.is_dir() and str(_OBS_ROOT) not in sys.path:
    sys.path.insert(0, str(_OBS_ROOT))

from telemetry.client import ObservabilityClient  # noqa: E402

_ENABLED = os.getenv("OBSERVABILITY_ENABLED", "true").lower() not in ("0", "false", "no")
_DEFAULT_EQUITY_INTERVAL_SEC = float(os.getenv("OBSERVABILITY_EQUITY_INTERVAL_SEC", "60"))


def _normalize_status(status: str | None) -> str:
    if not status:
        return "open"
    s = status.strip().lower()
    if s in ("open", "partial", "closed", "cancelled", "rejected"):
        return s
    if s == "opened":
        return "open"
    return s


def _normalize_side(side: Any) -> str:
    if side is None:
        return "long"
    if hasattr(side, "value"):
        side = side.value
    s = str(side).strip().lower()
    if s in ("long", "short", "buy", "sell"):
        return s
    if s == "long" or s.upper() == "LONG":
        return "long"
    if s == "short" or s.upper() == "SHORT":
        return "short"
    return "long"


class SafeObservability:
    """Thin, safe facade over ObservabilityClient for Easy ORB SO."""

    def __init__(self) -> None:
        self._client: ObservabilityClient | None = None
        self._last_equity_snapshot_ts: float = 0.0
        self.equity_interval_sec = _DEFAULT_EQUITY_INTERVAL_SEC

    def _get_client(self) -> ObservabilityClient:
        if self._client is None:
            self._client = ObservabilityClient(
                strategy_name=os.getenv("STRATEGY_NAME", "easy_orb_so"),
                strategy_group=os.getenv("STRATEGY_GROUP", "easy_orb"),
                deployment_project=os.getenv("DEPLOYMENT_PROJECT", "etrade-project"),
                environment=os.getenv("ENVIRONMENT", "demo"),
                execution_mode=os.getenv("EXECUTION_MODE", "demo"),
            )
        return self._client

    def _run(self, label: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
        if not _ENABLED:
            return None
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            log.warning("observability %s failed (non-fatal): %s", label, exc)
            return None

    def track_trade(self, **kwargs: Any) -> str | None:
        if "pnl" in kwargs and "realized_pnl" not in kwargs:
            kwargs["realized_pnl"] = kwargs.pop("pnl")
        if "status" in kwargs:
            kwargs["status"] = _normalize_status(kwargs["status"])
        if "side" in kwargs:
            kwargs["side"] = _normalize_side(kwargs["side"])
        if kwargs.get("status") == "closed" and not kwargs.get("closed_at"):
            kwargs["closed_at"] = datetime.now(timezone.utc)
        return self._run(
            "track_trade",
            self._get_client().track_trade,
            **kwargs,
        )

    def track_signal(self, **kwargs: Any) -> str | None:
        if "confidence" in kwargs and "strength" not in kwargs:
            kwargs["strength"] = kwargs.pop("confidence")
        return self._run(
            "track_signal",
            self._get_client().track_signal,
            **kwargs,
        )

    def track_equity_snapshot(self, **kwargs: Any) -> str | None:
        if "realized_pnl" in kwargs and "realized_pnl_day" not in kwargs:
            kwargs["realized_pnl_day"] = kwargs.pop("realized_pnl")
        open_positions = kwargs.pop("open_positions", None)
        active_symbols = kwargs.pop("active_symbols", None)
        extra = dict(kwargs.pop("metadata_json", None) or {})
        if open_positions is not None:
            extra["open_positions"] = open_positions
        if active_symbols is not None:
            extra["active_symbols"] = active_symbols
        if extra:
            kwargs["metadata_json"] = extra
        return self._run(
            "track_equity_snapshot",
            self._get_client().track_equity_snapshot,
            **kwargs,
        )

    def track_lifecycle_event(self, **kwargs: Any) -> str | None:
        return self._run(
            "track_lifecycle_event",
            self._get_client().track_lifecycle_event,
            **kwargs,
        )

    def emit_so_signal_collected(self, signal_dict: dict[str, Any]) -> str | None:
        """ORB SO signal accepted during 7:15–7:30 collection."""
        symbol = signal_dict.get("symbol") or signal_dict.get("original_symbol")
        if not symbol:
            return None

        signal_id = signal_dict.get("observability_signal_id")
        if not signal_id:
            signal_id = str(uuid.uuid4())
            signal_dict["observability_signal_id"] = signal_id

        meta = signal_dict.get("metadata") if isinstance(signal_dict.get("metadata"), dict) else {}
        orb_high = signal_dict.get("orb_high")
        orb_low = signal_dict.get("orb_low")
        orb_range = None
        if orb_high is not None and orb_low is not None:
            try:
                orb_range = float(orb_high) - float(orb_low)
            except (TypeError, ValueError):
                orb_range = None

        side = signal_dict.get("side") or "LONG"
        breakout_direction = "long" if str(side).upper() == "LONG" else "short"

        metadata_json = {
            "orb_range": orb_range,
            "orb_high": orb_high,
            "orb_low": orb_low,
            "breakout_direction": breakout_direction,
            "volume_ratio": meta.get("volume_ratio") or meta.get("orb_volume_ratio"),
            "priority_score": signal_dict.get("priority_score"),
            "rs_vs_spy": signal_dict.get("rs_vs_spy"),
            "collection_phase": "orb_so_scan",
        }

        self.track_lifecycle_event(
            event_type="signal_generated",
            event_category="session",
            symbol=symbol,
            signal_id=signal_id,
            message="ORB SO signal collected",
            payload={"side": side, "confidence": signal_dict.get("confidence")},
        )

        return self.track_signal(
            signal_id=signal_id,
            symbol=symbol,
            signal_type="orb_breakout",
            direction=breakout_direction,
            strength=signal_dict.get("confidence"),
            status="generated",
            signal_metadata=dict(meta),
            metadata_json=metadata_json,
        )

    def emit_so_trade_opened(
        self,
        *,
        trade_id: str,
        symbol: str,
        side: Any,
        entry_price: float,
        quantity: float,
        confidence: float | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        meta = {
            "strategy_phase": "entry",
            "confidence": confidence,
            **(metadata_json or {}),
        }
        self.track_trade(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            status="open",
            metadata_json=meta,
        )
        self.track_lifecycle_event(
            event_type="trade_opened",
            event_category="execution",
            symbol=symbol,
            trade_id=trade_id,
            message="ORB SO trade opened",
            payload=meta,
        )

    def emit_so_trade_closed(
        self,
        *,
        trade_id: str,
        symbol: str,
        side: Any,
        quantity: float,
        entry_price: float,
        exit_price: float,
        pnl: float,
        exit_reason: str,
        opened_at: datetime | None = None,
        metadata_json: dict[str, Any] | None = None,
    ) -> None:
        closed_at = datetime.now(timezone.utc)
        duration_seconds = None
        if opened_at:
            try:
                duration_seconds = (closed_at - opened_at).total_seconds()
            except Exception:
                duration_seconds = None

        meta = {
            "exit_reason": exit_reason,
            "duration_seconds": duration_seconds,
            **(metadata_json or {}),
        }
        self.track_trade(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            status="closed",
            realized_pnl=pnl,
            closed_at=closed_at,
            opened_at=opened_at or closed_at,
            metadata_json=meta,
        )
        self.track_lifecycle_event(
            event_type="trade_closed",
            event_category="execution",
            symbol=symbol,
            trade_id=trade_id,
            message=f"ORB SO trade closed: {exit_reason}",
            payload=meta,
        )

    def emit_so_risk_rejected(
        self,
        *,
        symbol: str,
        reason: str,
        signal_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.track_lifecycle_event(
            event_type="risk_rejected",
            event_category="risk",
            symbol=symbol,
            signal_id=signal_id,
            severity="warning",
            message=reason,
            payload=extra or {},
        )

    def maybe_emit_so_equity_snapshot(self, trading_system: Any) -> None:
        """Periodic equity sample (default every 60s)."""
        now = time.time()
        if now - self._last_equity_snapshot_ts < self.equity_interval_sec:
            return
        self._last_equity_snapshot_ts = now

        equity = None
        realized_pnl = None
        unrealized_pnl = None
        open_positions = 0
        active_symbols: list[str] = []

        try:
            if getattr(trading_system, "mock_executor", None):
                ex = trading_system.mock_executor
                equity = float(getattr(ex, "account_balance", 0) or 0)
                realized_pnl = float(getattr(ex, "total_pnl", 0) or 0)
                active = getattr(ex, "active_trades", {}) or {}
                open_positions = len(active)
                active_symbols = [t.symbol for t in active.values() if hasattr(t, "symbol")]
                unrealized = sum(
                    float(getattr(t, "unrealized_pnl", 0) or 0) for t in active.values()
                )
                unrealized_pnl = unrealized
            elif getattr(trading_system, "trade_manager", None) and hasattr(
                trading_system.trade_manager, "etrade_trading"
            ):
                et = trading_system.trade_manager.etrade_trading
                if et and hasattr(et, "get_account_summary"):
                    summary = et.get_account_summary()
                    if isinstance(summary, dict):
                        equity = float(summary.get("account_value") or summary.get("equity") or 0)
                        realized_pnl = float(summary.get("realized_pnl") or 0)
                        unrealized_pnl = float(summary.get("unrealized_pnl") or 0)
        except Exception as exc:
            log.debug("observability equity snapshot skipped: %s", exc)
            return

        if equity is None:
            return

        try:
            if getattr(trading_system, "stealth_trailing", None):
                snap = trading_system.stealth_trailing.snapshot()
                if isinstance(snap, dict) and snap.get("positions"):
                    open_positions = max(open_positions, len(snap["positions"]))
                    active_symbols = list(snap["positions"].keys())[:50]
        except Exception:
            pass

        self.track_equity_snapshot(
            equity=equity,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            open_positions=open_positions,
            active_symbols=active_symbols,
            source="orb_so_periodic",
        )

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:
                log.warning("observability client close failed: %s", exc)


observability = SafeObservability()
