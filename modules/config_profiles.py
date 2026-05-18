"""
Profile resolver for Easy ORB Strategy — bundles operator-facing presets.

Precedence (applied in ConfigLoader after the seven canonical .env files merge):
  1) Explicit key in merged config / os.environ (unchanged — files win over profile fill)
  2) Profile bundle default for keys still absent from merged config (ORB / Trendline execution
     bundles merge `modules/orb0dte_execution_defaults.py` + `modules/trendline_entry_defaults.py`
     first, then overlay path .env lines).
  3) Code fallbacks at call sites (unchanged)

Default `balanced` / `balanced_open` presets match repo .env-derived bundles. Other named presets apply
small bounded deltas for operator tuning without changing production defaults when profile keys stay at defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, FrozenSet, Mapping, Tuple

_STRAT_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_DIR = _STRAT_ROOT / "configs"


def _parse_env_file(path: Path) -> Dict[str, str]:
    """Parse KEY=value lines (same rules as ConfigLoader: strip, skip # comments)."""
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, value = s.split("=", 1)
        key = key.strip()
        value = value.strip()
        if "#" in value:
            value = value.split("#")[0].strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        out[key] = value
    return out


def _filter_by_prefixes(data: Mapping[str, str], prefixes: Tuple[str, ...]) -> Dict[str, str]:
    return {k: v for k, v in data.items() if any(k.startswith(p) for p in prefixes)}


ORB_0DTE_EXECUTION_PREFIXES: Tuple[str, ...] = (
    "0DTE_CONVEX_",
    "0DTE_CHOP_",
    "0DTE_CHAIN_HEALTH_",
    "ORB_0DTE_CHAIN_HEALTH_FALLBACK_",
    "0DTE_SELECTOR_FAILOVER_",
    "0DTE_FALLBACK_",
    "0DTE_LIQUIDITY_RELAX_",
    "ORB_0DTE_OVEREXTENSION_",
    "0DTE_PRIORITY_LEGACY_",
    "0DTE_PRIORITY_RANK_",
    "0DTE_PRIORITY_",
    "0DTE_AUTO_PARTIAL_",
    "0DTE_PARTIAL_",
    "0DTE_RUNNER_",
    "0DTE_DEBIT_HARD_STOP_",
    "0DTE_DEBIT_TIME_STOP_",
    "0DTE_DEBIT_FAIL_SAFE_",
    "0DTE_LOTTO_HARD_STOP_",
    "0DTE_LOTTO_TIME_STOP_",
    "0DTE_LOTTO_FAIL_SAFE_",
    "0DTE_FIRST_PROFIT_",
    "0DTE_SECOND_PROFIT_",
)

SO_PROFILE_PREFIXES: Tuple[str, ...] = (
    "SO_CONTINUATION_MOMENTUM_WEIGHT",
    "SO_EXHAUSTION_PENALTY_WEIGHT",
    "SO_MAX_EXTENSION_SOFT_PENALTY",
    "SO_ORB_RANGE_SOFT_PENALTY",
    "SO_MOMENTUM_DECELERATION_PENALTY",
    "SO_WINNER_",
    "SO_ADAPTIVE_EXPENSE_",
    "SO_ADAPTIVE_PROGRESSIVE_TARGETS",
)

TRENDLINE_ENTRY_EXCLUDE_PREFIXES: Tuple[str, ...] = (
    "TRENDLINE_MONITOR",
    "TRENDLINE_POSITION_MONITOR",
    "TRENDLINE_WATCH_",
    "TRENDLINE_GLOBAL_",
)

OPTIONS_EXIT_PREFIXES: Tuple[str, ...] = (
    "OPTION_STEALTH_",
    "OPTION_QUOTE_",
    "OPTION_0DTE_FAST_",
    "ORB_0DTE_SPREAD_OPEN_GRACE_",
)

# Keys that participate in execution / chain / selector even without the prefixes above
ORB_0DTE_EXECUTION_EXTRA_KEYS: FrozenSet[str] = frozenset(
    {
        "0DTE_EXECUTION_STRICTNESS_PROFILE",
        "0DTE_MAX_EXECUTION_CANDIDATES",
        "0DTE_MIN_VIABILITY_THRESHOLD",
        "0DTE_EXTENSION_THRESHOLD_PCT",
        "0DTE_CHAIN_FETCH_RETRY_ATTEMPTS",
        "0DTE_CHAIN_FETCH_RETRY_DELAY_SECONDS",
        "0DTE_MIN_CHAIN_STRIKES",
        "0DTE_MAX_AVG_SPREAD_PCT_PRE_8_PT",
        "0DTE_MAX_AVG_SPREAD_PCT_POST_8_PT",
        "0DTE_RECHECK_BREAKDOWN_MULTIPLIER",
        "ORB_0DTE_CHAIN_LATENCY_GOOD_MS",
        "ORB_0DTE_CHAIN_LATENCY_WARNING_MS",
        "ORB_0DTE_CHAIN_LATENCY_CRITICAL_MS",
    }
)


def _repo_orb0dte_flat() -> Dict[str, str]:
    return _parse_env_file(_CONFIG_DIR / "ORB0DTE.env")


def _repo_orbso_flat() -> Dict[str, str]:
    return _parse_env_file(_CONFIG_DIR / "ORBSO.env")


def _repo_trendline_flat() -> Dict[str, str]:
    return _parse_env_file(_CONFIG_DIR / "Trendline0DTE.env")


def _orb0dte_execution_bundle_from_repo() -> Dict[str, str]:
    from . import orb0dte_execution_defaults as oed

    d = _repo_orb0dte_flat()
    merged = dict(oed.ORB_0DTE_EXECUTION_BASE_DEFAULTS)
    file_part = dict(_filter_by_prefixes(d, ORB_0DTE_EXECUTION_PREFIXES))
    merged.update(file_part)
    for k in ORB_0DTE_EXECUTION_EXTRA_KEYS:
        if k in d:
            merged[k] = d[k]
    return merged


def _so_bundle_from_repo() -> Dict[str, str]:
    d = _repo_orbso_flat()
    out = dict(_filter_by_prefixes(d, SO_PROFILE_PREFIXES))
    # Explicit SO adaptive list key
    if "SO_ADAPTIVE_PROGRESSIVE_TARGETS" in d:
        out["SO_ADAPTIVE_PROGRESSIVE_TARGETS"] = d["SO_ADAPTIVE_PROGRESSIVE_TARGETS"]
    return out


def _trendline_entry_bundle_from_repo() -> Dict[str, str]:
    from . import trendline_entry_defaults as tled

    d = _repo_trendline_flat()
    out: Dict[str, str] = dict(tled.TRENDLINE_ENTRY_BASE_DEFAULTS)
    for k, v in d.items():
        if not k.startswith("TRENDLINE_"):
            continue
        if any(k.startswith(p) for p in TRENDLINE_ENTRY_EXCLUDE_PREFIXES):
            continue
        out[k] = v
    return out


def _options_exit_bundle_from_repo() -> Dict[str, str]:
    """
    Exit / stealth / quote knobs: Shared.env canonical OPTION_* / ORB_0DTE_SPREAD_* / OPTION_0DTE_FAST_*,
    then ORB0DTE / Trendline overrides (path expert knobs), then Risk STEALTH_*.
    """
    merged: Dict[str, str] = {}
    shared = _parse_env_file(_CONFIG_DIR / "Shared.env")
    for k, v in shared.items():
        if any(k.startswith(p) for p in OPTIONS_EXIT_PREFIXES):
            merged[k] = v
    for path in (_CONFIG_DIR / "ORB0DTE.env", _CONFIG_DIR / "Trendline0DTE.env"):
        part = _parse_env_file(path)
        for k, v in part.items():
            if any(k.startswith(p) for p in OPTIONS_EXIT_PREFIXES):
                merged[k] = v
    risk = _parse_env_file(_CONFIG_DIR / "Risk.env")
    for k, v in risk.items():
        if k.startswith("STEALTH_"):
            merged[k] = v
    return merged


def _deep_copy_str_dict(d: Mapping[str, str]) -> Dict[str, str]:
    return {str(k): str(v) for k, v in d.items()}


def _fmt_num(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    s = f"{x:.8f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _scale_key(d: Dict[str, str], key: str, mult: float) -> None:
    if key not in d:
        return
    try:
        v = float(d[key])
        d[key] = _fmt_num(v * mult)
    except (TypeError, ValueError):
        pass


def _bump_key(d: Dict[str, str], key: str, delta: float, lo: float | None = None, hi: float | None = None) -> None:
    if key not in d:
        return
    try:
        v = float(d[key]) + delta
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        d[key] = _fmt_num(v)
    except (TypeError, ValueError):
        pass


def _bump_int_key(d: Dict[str, str], key: str, delta: int, lo: int, hi: int) -> None:
    if key not in d:
        return
    try:
        v = int(float(d[key])) + int(delta)
        d[key] = str(max(lo, min(hi, v)))
    except (TypeError, ValueError):
        pass


def _fork_so(name: str, base: Dict[str, str]) -> Dict[str, str]:
    out = _deep_copy_str_dict(base)
    if name in ("", "balanced"):
        return out
    if name == "conservative":
        _scale_key(out, "SO_CONTINUATION_MOMENTUM_WEIGHT", 0.92)
        _scale_key(out, "SO_EXHAUSTION_PENALTY_WEIGHT", 1.08)
        _scale_key(out, "SO_MAX_EXTENSION_SOFT_PENALTY", 1.06)
        return out
    if name == "aggressive_open":
        _scale_key(out, "SO_CONTINUATION_MOMENTUM_WEIGHT", 1.10)
        _scale_key(out, "SO_EXHAUSTION_PENALTY_WEIGHT", 0.94)
        _bump_key(out, "SO_ORB_RANGE_SOFT_PENALTY", -0.005, lo=0.0)
        return out
    if name == "momentum_open":
        _scale_key(out, "SO_CONTINUATION_MOMENTUM_WEIGHT", 1.14)
        _scale_key(out, "SO_MOMENTUM_DECELERATION_PENALTY", 0.92)
        return out
    return out


def _fork_orb0dte(name: str, base: Dict[str, str]) -> Dict[str, str]:
    out = _deep_copy_str_dict(base)
    if name in ("", "balanced_open"):
        return out
    if name == "conservative":
        _bump_key(out, "0DTE_MIN_VIABILITY_THRESHOLD", 0.025, lo=0.2, hi=0.55)
        _bump_int_key(out, "0DTE_MAX_EXECUTION_CANDIDATES", -1, 3, 12)
        _scale_key(out, "0DTE_CHAIN_HEALTH_RELAX_DELTA_EXTRA", 0.92)
        _bump_key(out, "0DTE_CONVEX_MIN_SCORE", 0.03, lo=0.5, hi=0.95)
        return out
    if name == "aggressive_open":
        _bump_key(out, "0DTE_MIN_VIABILITY_THRESHOLD", -0.03, lo=0.2, hi=0.55)
        _bump_int_key(out, "0DTE_MAX_EXECUTION_CANDIDATES", 1, 3, 12)
        _scale_key(out, "0DTE_CHAIN_HEALTH_RELAX_DELTA_EXTRA", 1.06)
        _bump_key(out, "0DTE_CONVEX_MIN_SCORE", -0.03, lo=0.5, hi=0.95)
        return out
    if name == "momentum_open":
        _bump_key(out, "0DTE_CONVEX_MIN_SCORE", -0.04, lo=0.5, hi=0.95)
        _scale_key(out, "0DTE_CHOP_MOMENTUM_MIN", 0.97)
        return out
    return out


def _fork_trendline(name: str, base: Dict[str, str]) -> Dict[str, str]:
    out = _deep_copy_str_dict(base)
    if name in ("", "balanced"):
        return out
    if name == "conservative":
        _scale_key(out, "TRENDLINE_BREAK_DISTANCE_MIN", 1.08)
        _bump_key(out, "TRENDLINE_MIN_VELOCITY_PCT", 0.0002, lo=0.0)
        _scale_key(out, "TRENDLINE_ENTRY_SCORE_MIN_IMPULSE", 1.06)
        _scale_key(out, "TRENDLINE_ENTRY_SCORE_MIN_CONTINUATION", 1.10)
        _scale_key(out, "TRENDLINE_ENTRY_SCORE_MIN_EXHAUSTION", 1.10)
        _scale_key(out, "TRENDLINE_ENTRY_SURVIVAL_SEC_MULT_DRIFT", 0.82)
        _scale_key(out, "TRENDLINE_ENTRY_SURVIVAL_SEC_MULT_EXHAUSTION", 0.82)
        _bump_int_key(out, "TRENDLINE_BOUNCEBACK_RECLAIM_MAX_BARS", -1, 1, 8)
        return out
    if name == "aggressive_open":
        _scale_key(out, "TRENDLINE_BREAK_DISTANCE_MIN", 0.92)
        _bump_key(out, "TRENDLINE_MIN_VELOCITY_PCT", -0.0002, lo=0.0)
        _scale_key(out, "TRENDLINE_ENTRY_SCORE_MIN_IMPULSE", 0.93)
        _scale_key(out, "TRENDLINE_ENTRY_SCORE_MIN_CONTINUATION", 0.92)
        _scale_key(out, "TRENDLINE_ENTRY_SCORE_MIN_EXHAUSTION", 0.92)
        _scale_key(out, "TRENDLINE_ENTRY_SURVIVAL_SEC_MULT_DRIFT", 1.14)
        _scale_key(out, "TRENDLINE_ENTRY_SURVIVAL_SEC_MULT_EXHAUSTION", 1.12)
        _bump_int_key(out, "TRENDLINE_ENTRY_SURVIVAL_EXTRA_BARS_DRIFT", 1, 0, 12)
        _bump_int_key(out, "TRENDLINE_ENTRY_SURVIVAL_EXTRA_BARS_EXHAUSTION", 1, 0, 12)
        return out
    if name == "momentum_breakout":
        _scale_key(out, "TRENDLINE_MIN_BREAK_QUALITY_SCORE", 1.05)
        _bump_key(out, "TRENDLINE_MIN_VELOCITY_PCT", -0.0003, lo=0.0)
        return out
    if name == "retest_friendly":
        _scale_key(out, "TRENDLINE_RETEST_LINE_BUFFER_PCT", 1.15)
        _bump_int_key(out, "TRENDLINE_RETEST_MAX_CHECKS", 1, 1, 5)
        return out
    return out


def _fork_options_exit(name: str, base: Dict[str, str]) -> Dict[str, str]:
    out = _deep_copy_str_dict(base)
    if name in ("", "balanced"):
        return out
    if name == "conservative":
        _bump_int_key(out, "OPTION_QUOTE_STALE_MAX_AGE_SECONDS", -10, 45, 600)
        _bump_int_key(out, "OPTION_QUOTE_EXIT_GRADE_MAX_AGE_SECONDS", -5, 10, 120)
        return out
    if name == "aggressive_capture":
        _bump_int_key(out, "OPTION_QUOTE_STALE_MAX_AGE_SECONDS", -20, 40, 600)
        _scale_key(out, "OPTION_STEALTH_ORB_TRAILING_TRIGGER_MULT", 0.95)
        return out
    if name == "runner_capture":
        _scale_key(out, "OPTION_STEALTH_ORB_PROFIT_LOCK_TRIGGER_PCT", 0.92)
        _scale_key(out, "OPTION_STEALTH_ORB_TRAILING_TRIGGER_MULT", 0.97)
        return out
    return out


_SO_BASE = _so_bundle_from_repo()
_ORB0_BASE = _orb0dte_execution_bundle_from_repo()
_TLINE_BASE = _trendline_entry_bundle_from_repo()
_OPT_BASE = _options_exit_bundle_from_repo()

# Primary presets — balanced* match checked-in repo defaults; other presets apply small bounded deltas.
ORB_0DTE_EXECUTION_PROFILES: Dict[str, Dict[str, str]] = {
    "balanced_open": _deep_copy_str_dict(_ORB0_BASE),
    "conservative": _fork_orb0dte("conservative", _ORB0_BASE),
    "aggressive_open": _fork_orb0dte("aggressive_open", _ORB0_BASE),
    "momentum_open": _fork_orb0dte("momentum_open", _ORB0_BASE),
}

SO_PROFILES: Dict[str, Dict[str, str]] = {
    "balanced": _deep_copy_str_dict(_SO_BASE),
    "conservative": _fork_so("conservative", _SO_BASE),
    "aggressive_open": _fork_so("aggressive_open", _SO_BASE),
    "momentum_open": _fork_so("momentum_open", _SO_BASE),
}

TRENDLINE_ENTRY_PROFILES: Dict[str, Dict[str, str]] = {
    "balanced": _deep_copy_str_dict(_TLINE_BASE),
    "conservative": _fork_trendline("conservative", _TLINE_BASE),
    "aggressive": _fork_trendline("aggressive_open", _TLINE_BASE),
    "aggressive_open": _fork_trendline("aggressive_open", _TLINE_BASE),
    "momentum_breakout": _fork_trendline("momentum_breakout", _TLINE_BASE),
    "retest_friendly": _fork_trendline("retest_friendly", _TLINE_BASE),
}

OPTIONS_EXIT_PROFILES: Dict[str, Dict[str, str]] = {
    "balanced": _deep_copy_str_dict(_OPT_BASE),
    "conservative": _fork_options_exit("conservative", _OPT_BASE),
    "aggressive_capture": _fork_options_exit("aggressive_capture", _OPT_BASE),
    "runner_capture": _fork_options_exit("runner_capture", _OPT_BASE),
}


def resolve_profile_defaults(
    *,
    so_profile: str,
    orb0dte_execution_profile: str,
    trendline_entry_profile: str,
    options_exit_profile: str,
) -> Dict[str, str]:
    """
    Merge profile bundles (SO → ORB0DTE execution → Trendline entry → options exit).
    Later groups in this sequence do not override earlier keys on collision.
    """
    merged: Dict[str, str] = {}
    for chunk in (
        SO_PROFILES.get(so_profile, SO_PROFILES["balanced"]),
        ORB_0DTE_EXECUTION_PROFILES.get(orb0dte_execution_profile, ORB_0DTE_EXECUTION_PROFILES["balanced_open"]),
        TRENDLINE_ENTRY_PROFILES.get(trendline_entry_profile, TRENDLINE_ENTRY_PROFILES["balanced"]),
        OPTIONS_EXIT_PROFILES.get(options_exit_profile, OPTIONS_EXIT_PROFILES["balanced"]),
    ):
        for k, v in chunk.items():
            if k not in merged:
                merged[k] = v
    return merged


def summarize_orb0dte_execution_profile(bundle: Mapping[str, str]) -> Dict[str, Any]:
    """Compact audit payload for ORB_0DTE_EXECUTION_PROFILE_RESOLVED."""
    def g(*names: str) -> Any:
        for n in names:
            if n in bundle:
                return bundle[n]
        return None

    return {
        "chain_strictness": g("0DTE_EXECUTION_STRICTNESS_PROFILE"),
        "fallback_widths": g("ORB_0DTE_CHAIN_HEALTH_FALLBACK_WIDTHS"),
        "fallback_delta_offsets": g("ORB_0DTE_CHAIN_HEALTH_FALLBACK_DELTA_OFFSETS"),
        "fallback_itm_delta_bump": g("ORB_0DTE_CHAIN_HEALTH_FALLBACK_ITM_DELTA_BUMP"),
        "liquidity_relax_spread_mult": g("0DTE_LIQUIDITY_RELAX_SPREAD_MULT"),
        "liquidity_relax_oi_mult": g("0DTE_LIQUIDITY_RELAX_OI_MULT"),
        "liquidity_relax_volume_mult": g("0DTE_LIQUIDITY_RELAX_VOLUME_MULT"),
        "overextension_soft_threshold": g(
            "ORB_0DTE_OVEREXTENSION_SOFT_THRESHOLD",
            "0DTE_EXTENSION_THRESHOLD_PCT",
        ),
        "max_execution_candidates": g("0DTE_MAX_EXECUTION_CANDIDATES"),
        "chain_health_relax_delta_extra": g("0DTE_CHAIN_HEALTH_RELAX_DELTA_EXTRA"),
    }
