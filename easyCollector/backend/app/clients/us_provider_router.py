"""
Easy Collector - US Data Provider Router
Healthcheck gate, failover across polygon/alpaca/yfinance, and fail-open when all are down.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any

from app.config import get_settings

log = logging.getLogger(__name__)


def _build_provider_order(settings) -> List[str]:
    primary = (settings.us_data_provider or "yfinance").strip().lower()
    fallbacks = [p for p in settings.us_provider_fallbacks_list if p != primary]
    return [primary] + fallbacks


def _get_client_instance(name: str, yfinance_client=None):
    if name == "polygon":
        from app.clients.polygon_client import PolygonClient
        return PolygonClient()
    if name == "alpaca":
        from app.clients.alpaca_client import AlpacaClient
        return AlpacaClient()
    if name == "yfinance":
        from app.clients.yfinance_client import YFinanceClient
        return yfinance_client if yfinance_client is not None else YFinanceClient()
    return None


def run_healthcheck(yfinance_client=None) -> Tuple[Optional[Any], Optional[str], Dict]:
    """
    Run healthcheck gate: try primary then fallbacks. For each,
    - polygon: use client.healthcheck() (includes real fetch).
    - yfinance: client.healthcheck() then real mini fetch.
    - alpaca: client.healthcheck().

    Returns:
        (client, provider_name, health_result)
        If all fail: (None, None, {"status": "all_failed", "us_collection_status": "SKIPPED_PROVIDER_DOWN", ...}).
    """
    settings = get_settings()
    if (settings.us_data_provider or "").strip().lower() == "disabled":
        return None, None, {"status": "disabled", "us_collection_status": "SKIPPED_PROVIDER_DOWN", "provider": "disabled"}

    order = _build_provider_order(settings)
    sym = settings.us_provider_healthcheck_symbol
    bars = max(settings.us_provider_healthcheck_bars, 12)
    health_result = {"status": "all_failed", "us_collection_status": "SKIPPED_PROVIDER_DOWN", "tried": []}

    for name in order:
        client = _get_client_instance(name, yfinance_client=yfinance_client)
        if client is None:
            health_result["tried"].append({"provider": name, "reason": "no_client"})
            continue

        if name == "polygon":
            h = client.healthcheck()
            health_result["tried"].append({"provider": name, "health": h})
            if h.get("status") == "healthy" and h.get("row_count", 0) >= min(12, bars):
                health_result["status"] = "healthy"
                health_result["us_collection_status"] = "OK"
                health_result["provider_used"] = name
                health_result["provider_health"] = h
                return client, name, health_result

        elif name == "yfinance":
            h = client.healthcheck()
            if h.get("status") != "healthy":
                health_result["tried"].append({"provider": name, "health": h, "reason": "healthcheck_not_healthy"})
                continue
            # YFinanceClient.healthcheck() does a real 5m fetch and enforces rows/NaNs/last_bar_age
            if h.get("row_count", 0) < min(12, bars - 2):
                health_result["tried"].append({"provider": name, "health": h, "reason": "healthcheck_row_count_low"})
                continue
            health_result["status"] = "healthy"
            health_result["us_collection_status"] = "OK"
            health_result["provider_used"] = name
            health_result["provider_health"] = h
            return client, name, health_result

        elif name == "alpaca":
            h = client.healthcheck()
            health_result["tried"].append({"provider": name, "health": h})
            if h.get("status") == "healthy":
                health_result["status"] = "healthy"
                health_result["us_collection_status"] = "OK"
                health_result["provider_used"] = name
                health_result["provider_health"] = h
                return client, name, health_result

    return None, None, health_result


def is_skipped(health_result: Dict) -> bool:
    return health_result.get("us_collection_status") == "SKIPPED_PROVIDER_DOWN"
