# modules/config_loader.py
"""
Unified Configuration Loader for Easy ORB Strategy
Loads and manages configuration from multiple .env files based on mode and environment

**Profile layer (May 2026):** After the seven files and optional `advanced` / `quantum` preset, `modules/config_profiles.py`
fills keys that are still absent (`SO_PROFILE`, `ORB_0DTE_EXECUTION_PROFILE`, `TRENDLINE_ENTRY_PROFILE`, `OPTIONS_EXIT_PROFILE`).
Canonical alias normalization (e.g. `0DTE_EXTENSION_THRESHOLD_PCT` → `ORB_0DTE_OVEREXTENSION_SOFT_THRESHOLD`) runs immediately after the seven files (before presets).
Startup audits: `CONFIG_PROFILE_RESOLUTION_COMPLETE`, `ORB_0DTE_EXECUTION_PROFILE_RESOLVED`, duplicate summaries, sizing/monitoring precedence, **`CONFIG_CROSS_FILE_DUPLICATE_UNRESOLVED`** (multi-file same key with different values; see `configs/config_manifest.yaml` policy).

Operator-canonical seven (load order — later overrides earlier): Data.env, Shared.env,
ORBSO.env, ORB0DTE.env, Trendline0DTE.env, Risk.env, Alerts.env. Strategy/Alerts files start with a comment manifest of Shared.env keys
(values for those keys live only in Shared.env). Data.env = former data-providers + broker-config;
Risk.env = former position-sizing + risk-management + slip-guard.

**Strategy mode** `advanced` / `quantum` overlays: `modules/strategy_mode_presets.py`
(applied after the seven files). **`standard`** uses the merged repo defaults in the
seven `.env` files only. **`ENVIRONMENT`** is set from `load_configuration(...,
environment=...)`; production tuning is via Cloud Run / shell `os.environ` overrides,
not a separate `environments/*.env` file. Then **secretsprivate/*.env** (non-production).

The seven files contain former **base.env**, **automation.env**, **deployment.env**, and
**trading-parameters.env** content (distributed May 2026). **Data.env** also includes the
old data-providers + broker-config merge; **Risk.env** includes position-sizing +
risk-management + slip-guard.

Last Updated: May 4, 2026 — Path-scoped ORBSO.env / ORB0DTE.env / Trendline0DTE.env after Shared.env.
"""

import os
import json
import logging
from typing import Dict, Any, Optional, Set, Tuple, List
from pathlib import Path

log = logging.getLogger("config_loader")

# Startup ownership / external override telemetry (May 2026)
_SENSITIVE_ENV_PREFIXES: Tuple[str, ...] = (
    "TRENDLINE_FAST_PATH_",
    "OPTION_STEALTH_",
    "OPTION_QUOTE_",
    "ORB_SPREAD_",
    "ORB_OPTIONS_SPREAD_",
    "TRENDLINE_OPTION_",
    "0DTE_",
)

# One-shot startup diagnostics (avoid log spam across hot paths)
_CONFIG_DEPRECATED_ALIAS_LOGGED: Set[str] = set()
_CONFIG_CANONICAL_OVERRIDE_LOGGED: Set[str] = set()
_CONFIG_STEALTH_OWNERSHIP_LOGGED: Set[str] = set()
_CONFIG_PROFILE_AUDIT_DONE = False

_ORIGINAL_OS_GETENV = os.getenv
_CONFIG_GETENV_HOOK_INSTALLED = False
_CONFIG_MISSING_KEYS_LOGGED = set()


def _looks_like_config_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    s = key.strip()
    if not s:
        return False
    # Keep noise low: config keys in this app are uppercase snake-case.
    return s.upper() == s and "_" in s


def _log_config_missing_key_once(key: str):
    if key in _CONFIG_MISSING_KEYS_LOGGED:
        return
    _CONFIG_MISSING_KEYS_LOGGED.add(key)
    log.warning(
        "CONFIG_MISSING_KEY | key=%s | fallback_used=true",
        key,
    )


def _install_os_getenv_missing_key_hook():
    global _CONFIG_GETENV_HOOK_INSTALLED
    if _CONFIG_GETENV_HOOK_INSTALLED:
        return

    def _getenv_with_missing_key_log(key, default=None):
        value = _ORIGINAL_OS_GETENV(key, default)
        if _looks_like_config_key(key) and (key not in os.environ):
            _log_config_missing_key_once(str(key))
        return value

    os.getenv = _getenv_with_missing_key_log
    _CONFIG_GETENV_HOOK_INSTALLED = True

class ConfigLoader:
    """
    Unified configuration loader that combines multiple .env files
    based on strategy mode, automation mode, and environment
    """
    
    def __init__(self, base_path: str = "configs"):
        self.base_path = Path(base_path)
        self.secrets_path = Path("secretsprivate")
        self.config = {}
        self.loaded_files = []
        self._key_sources = {}
        self._duplicate_keys_logged = set()
        self._last_win_path: Dict[str, str] = {}
        self._resolved_snapshot: Dict[str, str] = {}
        self._get_value_cache: Dict[Tuple[str, bool], Any] = {}
        self._get_value_cache_max = 768
        self._bootstrap_env_keys: Set[str] = set()
    
    def load_configuration(
        self, 
        strategy_mode: str = "standard",
        automation_mode: str = "off", 
        environment: str = "development"
    ) -> Dict[str, Any]:
        """
        Load unified configuration based on mode and environment
        
        Args:
            strategy_mode: Strategy mode (standard, advanced, quantum)
            automation_mode: Automation mode (off, demo, live)
            environment: Environment (development, production, sandbox)
        
        Returns:
            Dict containing all configuration values
        """
        
        log.info(f"Loading configuration: strategy={strategy_mode}, "
                f"automation={automation_mode}, environment={environment}")
        
        # Clear previous configuration
        self.config = {}
        self.loaded_files = []
        self._key_sources = {}
        self._duplicate_keys_logged = set()
        self._last_win_path = {}
        self._resolved_snapshot = {}
        self._get_value_cache = {}
        self._bootstrap_env_keys = set(os.environ.keys())

        # Passive runtime visibility for getenv fallbacks (_env_float/_env_int and raw os.getenv call sites).
        _install_os_getenv_missing_key_hook()
        
        # Canonical seven (order matters; later files override earlier on duplicate keys)
        self._load_env_file(self.base_path / "Data.env")  # data/broker + infra + paths (inc. former base/deploy slices)
        self._load_env_file(self.base_path / "Shared.env")  # app-wide + former base/automation slices
        self._load_env_file(self.base_path / "ORBSO.env")
        self._load_env_file(self.base_path / "ORB0DTE.env")
        self._load_env_file(self.base_path / "Trendline0DTE.env")
        self._load_env_file(self.base_path / "Risk.env")  # sizing + risk + slip + former trading-parameters risk keys
        self._load_env_file(self.base_path / "Alerts.env")

        self._scan_same_file_duplicates()
        self._resolve_canonical_aliases()

        # Advanced / quantum: former configs/modes/*.env (May 2026 — presets in code)
        self._apply_strategy_mode_preset(strategy_mode)

        self._apply_profile_defaults_layer()
        self._audit_stealth_quote_cross_path_duplicates()
        self._log_sizing_and_monitoring_precedence()

        # Set runtime configuration
        self.config["STRATEGY_MODE"] = strategy_mode
        self.config["AUTOMATION_MODE"] = automation_mode
        self.config["ENVIRONMENT"] = environment
        
        # Load secrets from secretsprivate/ folder (local development)
        # Production should use Google Secret Manager instead
        self._load_secrets()

        self._finalize_startup_config_audits()
        
        # Validate configuration
        self._validate_configuration()
        
        log.info(f"Configuration loaded from {len(self.loaded_files)} files")
        return self.config
    
    def _load_env_file(self, file_path: Path):
        """Load environment file if it exists"""
        if not file_path.exists():
            return
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    
                    # Parse key=value pairs
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Remove inline comments (everything after #)
                        if '#' in value:
                            value = value.split('#')[0].strip()
                        
                        # Remove quotes if present
                        if value.startswith('"') and value.endswith('"'):
                            value = value[1:-1]
                        elif value.startswith("'") and value.endswith("'"):
                            value = value[1:-1]

                        sources = self._key_sources.setdefault(key, [])
                        if str(file_path) not in sources:
                            sources.append(str(file_path))
                        if len(sources) > 1:
                            dup_token = (key, tuple(sources))
                            if dup_token not in self._duplicate_keys_logged:
                                log.info(
                                    "CONFIG_DUPLICATE_KEY | key=%s | sources=%s",
                                    key,
                                    sources,
                                )
                                self._duplicate_keys_logged.add(dup_token)
                        self.config[key] = value
                        self._last_win_path[key] = str(file_path)
                    else:
                        log.warning(f"Invalid line in {file_path}:{line_num}: {line}")
            
            self.loaded_files.append(str(file_path))
            log.debug(f"Loaded configuration from: {file_path}")
            
        except Exception as e:
            log.error(f"Error loading configuration file {file_path}: {e}")

    def _scan_same_file_duplicates(self) -> None:
        """Detect duplicate KEY= lines within a single canonical file (last assignment wins)."""
        for fname in (
            "Data.env",
            "Shared.env",
            "ORBSO.env",
            "ORB0DTE.env",
            "Trendline0DTE.env",
            "Risk.env",
            "Alerts.env",
        ):
            path = self.base_path / fname
            if not path.exists():
                continue
            seen: Dict[str, int] = {}
            for line_num, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key = line.split("=", 1)[0].strip()
                if key in seen:
                    log.info(
                        "CONFIG_DUPLICATE_KEY_SAME_FILE | file=%s | key=%s | first_line=%d | repeat_line=%d | last_wins=true",
                        fname,
                        key,
                        seen[key],
                        line_num,
                    )
                seen[key] = line_num

    def _resolve_canonical_aliases(self) -> None:
        """Normalize legacy env aliases into canonical keys (no silent conflicts)."""
        canon = "ORB_0DTE_OVEREXTENSION_SOFT_THRESHOLD"
        legacy = "0DTE_EXTENSION_THRESHOLD_PCT"
        c_raw = self.config.get(canon)
        l_raw = self.config.get(legacy)

        def _to_float(x: Any) -> Optional[float]:
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        if c_raw is None and l_raw is not None:
            self.config[canon] = l_raw
            token = f"{legacy}->{canon}"
            if token not in _CONFIG_DEPRECATED_ALIAS_LOGGED:
                log.info(
                    "CONFIG_DEPRECATED_ALIAS_USED | old_key=%s | new_key=%s",
                    legacy,
                    canon,
                )
                _CONFIG_DEPRECATED_ALIAS_LOGGED.add(token)
        elif c_raw is not None and l_raw is not None:
            fc, fl = _to_float(c_raw), _to_float(l_raw)
            if fc is not None and fl is not None and abs(fc - fl) > 1e-12:
                token = f"{canon}|{legacy}"
                if token not in _CONFIG_CANONICAL_OVERRIDE_LOGGED:
                    log.warning(
                        "CONFIG_CANONICAL_KEY_OVERRIDES_ALIAS | canonical_key=%s | alias_key=%s | canonical_value=%s | alias_value=%s",
                        canon,
                        legacy,
                        c_raw,
                        l_raw,
                    )
                    _CONFIG_CANONICAL_OVERRIDE_LOGGED.add(token)

        dep_used = sorted(k for k in _CONFIG_DEPRECATED_ALIAS_LOGGED if "->" in k)
        if dep_used:
            log.info("CONFIG_DEPRECATED_KEYS_SUMMARY | aliases=%s", ",".join(dep_used))

    def _apply_profile_defaults_layer(self) -> None:
        """Fill missing keys from profile bundles (repo .env files remain authoritative when present)."""
        from modules import config_profiles as cp

        raw_so = str(self.config.get("SO_PROFILE", "") or "").strip().lower()
        so_p = raw_so if raw_so in cp.SO_PROFILES else "balanced"
        if raw_so and raw_so not in cp.SO_PROFILES:
            log.warning("CONFIG_PROFILE_UNKNOWN | profile_key=SO_PROFILE | value=%s | using_balanced=true", raw_so)

        raw_orb = str(self.config.get("ORB_0DTE_EXECUTION_PROFILE", "") or "").strip().lower()
        orb_p = raw_orb if raw_orb in cp.ORB_0DTE_EXECUTION_PROFILES else "balanced_open"
        if raw_orb and raw_orb not in cp.ORB_0DTE_EXECUTION_PROFILES:
            log.warning(
                "CONFIG_PROFILE_UNKNOWN | profile_key=ORB_0DTE_EXECUTION_PROFILE | value=%s | using_balanced_open=true",
                raw_orb,
            )

        raw_tl = str(self.config.get("TRENDLINE_ENTRY_PROFILE", "") or "").strip().lower()
        tl_p = raw_tl if raw_tl in cp.TRENDLINE_ENTRY_PROFILES else "balanced"
        if raw_tl and raw_tl not in cp.TRENDLINE_ENTRY_PROFILES:
            log.warning(
                "CONFIG_PROFILE_UNKNOWN | profile_key=TRENDLINE_ENTRY_PROFILE | value=%s | using_balanced=true",
                raw_tl,
            )

        raw_opt = str(self.config.get("OPTIONS_EXIT_PROFILE", "") or "").strip().lower()
        opt_p = raw_opt if raw_opt in cp.OPTIONS_EXIT_PROFILES else "balanced"
        if raw_opt and raw_opt not in cp.OPTIONS_EXIT_PROFILES:
            log.warning(
                "CONFIG_PROFILE_UNKNOWN | profile_key=OPTIONS_EXIT_PROFILE | value=%s | using_balanced=true",
                raw_opt,
            )

        merged = cp.resolve_profile_defaults(
            so_profile=so_p,
            orb0dte_execution_profile=orb_p,
            trendline_entry_profile=tl_p,
            options_exit_profile=opt_p,
        )
        filled = 0
        for k, v in merged.items():
            if k not in self.config:
                self.config[k] = v
                self._last_win_path.setdefault(k, "modules/config_profiles.py")
                filled += 1

        log.info(
            "CONFIG_PROFILE_RESOLUTION_COMPLETE | SO_PROFILE=%s | ORB_0DTE_EXECUTION_PROFILE=%s | "
            "TRENDLINE_ENTRY_PROFILE=%s | OPTIONS_EXIT_PROFILE=%s | profile_gap_fill_keys=%d",
            so_p,
            orb_p,
            tl_p,
            opt_p,
            filled,
        )

        bundle = cp.ORB_0DTE_EXECUTION_PROFILES.get(orb_p, cp.ORB_0DTE_EXECUTION_PROFILES["balanced_open"])
        eff: Dict[str, Any] = {k: self.config.get(k, bundle.get(k)) for k in bundle}
        summary = cp.summarize_orb0dte_execution_profile(eff)
        log.info(
            "ORB_0DTE_EXECUTION_PROFILE_RESOLVED | profile=%s | summary=%s",
            orb_p,
            summary,
        )

    def _audit_stealth_quote_cross_path_duplicates(self) -> None:
        """OPTION_STEALTH_* / OPTION_QUOTE_* duplicated across ORB0DTE + Trendline — last file wins; log once."""
        for key, srcs in self._key_sources.items():
            if not (key.startswith("OPTION_STEALTH_") or key.startswith("OPTION_QUOTE_")):
                continue
            has_orb = any("ORB0DTE.env" in s for s in srcs)
            has_tl = any("Trendline0DTE.env" in s for s in srcs)
            if has_orb and has_tl and key not in _CONFIG_STEALTH_OWNERSHIP_LOGGED:
                log.warning(
                    "CONFIG_DUPLICATE_OWNERSHIP_WARNING | key=%s | owners=ORB0DTE,Trendline0DTE | runtime_last_wins_path=%s | "
                    "effective_value=%s | hint=centralize_under_OPTIONS_EXIT_PROFILE_in_Shared.env",
                    key,
                    self._last_win_path.get(key, ""),
                    self.config.get(key),
                )
                _CONFIG_STEALTH_OWNERSHIP_LOGGED.add(key)

    def _log_sizing_and_monitoring_precedence(self) -> None:
        log.info(
            "CONFIG_SIZING_PRECEDENCE_AUDIT | ORBSO=SO_ETF_sizing_and_MAX_CONCURRENT_TRADES | "
            "ORB0DTE=0DTE_MAX_POSITIONS_and_path_execution_caps | Trendline=TRENDLINE_MAX_OPEN_POSITIONS_and_path_caps | "
            "Shared=MAX_TOTAL_OPTION_POSITIONS_combined_ORB0DTE_plus_Trendline | "
            "Risk=MAX_OPEN_POSITIONS_global_safety_ceiling"
        )
        log.info(
            "CONFIG_MONITORING_PRECEDENCE_AUDIT | ORB0DTE=ORB_0DTE_POSITION_MONITOR_*_and_ORB_OPTIONS_MONITOR_* | "
            "Trendline=TRENDLINE_POSITION_MONITOR_*_and_TRENDLINE_WATCH_* | "
            "Shared=OPTIONS_MONITOR_SUMMARY_INTERVAL_SEC_and_OPTION_CHAIN_LATENCY_WINDOW_SIZE_fallbacks | "
            "OPTIONS_EXIT_PROFILE=OPTION_STEALTH_*_OPTION_QUOTE_*_heartbeat_degraded_policies"
        )

    def _emit_config_ownership_audit(self) -> None:
        """Single-line ownership / clutter telemetry for operators (May 2026)."""
        total = len(self.config)
        cross_dup_keys = [k for k, srcs in self._key_sources.items() if len(srcs) > 1]
        profile_filled = sum(
            1
            for _k, p in self._last_win_path.items()
            if str(p).replace("\\", "/").endswith("config_profiles.py")
        )
        by_owner: Dict[str, int] = {}
        for _k, p in self._last_win_path.items():
            if "secretsprivate" in p:
                leaf = "secretsprivate"
            else:
                leaf = Path(p).name
            by_owner[leaf] = by_owner.get(leaf, 0) + 1
        dep: List[str] = []
        try:
            from easyTrendline import trendline_config_loader as tcl

            dep = sorted(k for k in self.config if k in tcl._DEPRECATED_TRENDLINE_ENV_KEYS)
        except Exception:
            pass
        sens = sorted(
            k
            for k in self.config
            if k in self._bootstrap_env_keys and any(k.startswith(pref) for pref in _SENSITIVE_ENV_PREFIXES)
        )
        log.info(
            "CONFIG_OWNERSHIP_AUDIT | total_env_keys_loaded=%d | cross_file_duplicate_key_count=%d | "
            "profile_gap_fill_key_count=%d | deprecated_key_hits=%d | sensitive_bootstrap_env_overlap=%d | "
            "last_win_histogram=%s | duplicate_sample=%s",
            total,
            len(cross_dup_keys),
            profile_filled,
            len(dep),
            len(sens),
            json.dumps({k: by_owner[k] for k in sorted(by_owner.keys())}, separators=(",", ":")),
            ",".join(cross_dup_keys[:40]) + ("..." if len(cross_dup_keys) > 40 else ""),
        )
        if dep:
            log.info("CONFIG_OWNERSHIP_DEPRECATED | keys=%s", ",".join(dep))
        if sens:
            log.warning(
                "CONFIG_OWNERSHIP_SENSITIVE_ENV_PRESEED | keys=%s | hint=shell_or_cloud_env_may_override_repo_files_at_runtime",
                ",".join(sens[:60]) + ("..." if len(sens) > 60 else ""),
            )

    def _subset_audit(self, title: str, predicate) -> None:
        d = {k: self.config[k] for k in sorted(self.config) if predicate(k)}
        keys = sorted(d.keys())
        shown = keys[:72]
        d_out = {k: d[k] for k in shown}
        log.info("%s | total_matched=%d | logged=%d | subset=%s", title, len(keys), len(shown), json.dumps(d_out, separators=(",", ":")))

    def _emit_effective_path_audits(self) -> None:
        self._subset_audit(
            "ORB_SO_EFFECTIVE_CONFIG_AUDIT",
            lambda k: k.startswith("SO_")
            or k.startswith("ORB_WINDOW_")
            or k.startswith("ORR_")
            or k.startswith("ENABLE_ORB")
            or k in ("MAX_CONCURRENT_TRADES", "SO_PROFILE"),
        )
        self._subset_audit(
            "ORB_0DTE_EFFECTIVE_CONFIG_AUDIT",
            lambda k: k.startswith("0DTE_")
            or k.startswith("ORB_0DTE_")
            or k.startswith("ORB_OPTIONS_")
            or k in ("ENABLE_0DTE_STRATEGY", "ORB_0DTE_EXECUTION_PROFILE"),
        )
        self._subset_audit(
            "TRENDLINE_EFFECTIVE_CONFIG_AUDIT",
            lambda k: k.startswith("TRENDLINE_") or k in ("ENABLE_TRENDLINE_STRATEGY", "TRENDLINE_ENTRY_PROFILE"),
        )
        self._subset_audit(
            "OPTION_STEALTH_EFFECTIVE_CONFIG_AUDIT",
            lambda k: k.startswith("OPTION_STEALTH_")
            or k.startswith("OPTION_QUOTE_")
            or k.startswith("OPTION_0DTE_FAST_")
            or k.startswith("ORB_0DTE_SPREAD_OPEN_GRACE_"),
        )
        self._subset_audit(
            "RISK_EFFECTIVE_CONFIG_AUDIT",
            lambda k: k.startswith("STEALTH_")
            or k.startswith("SLIP_GUARD_")
            or k.startswith("RISK_")
            or k
            in (
                "MAX_OPEN_POSITIONS",
                "POSITION_ISOLATION_ENABLED",
                "IGNORE_MANUAL_POSITIONS",
                "MAX_RISK_PER_TRADE_PCT",
                "MAX_DRAWDOWN_PCT",
                "MAX_DAILY_LOSS_PCT",
                "STOP_LOSS_ATR_MULTIPLIER",
                "TAKE_PROFIT_ATR_MULTIPLIER",
                "TRAILING_STOP_ATR_MULTIPLIER",
            ),
        )

    def _finalize_startup_config_audits(self) -> None:
        global _CONFIG_PROFILE_AUDIT_DONE
        self._emit_config_ownership_audit()
        self._emit_effective_path_audits()
        self._resolved_snapshot = {str(k): str(v) for k, v in sorted(self.config.items())}
        self._audit_cross_file_duplicate_unresolved()
        cross_dup = [k for k, srcs in self._key_sources.items() if len(srcs) > 1]
        log.info(
            "CONFIG_DUPLICATE_KEYS_SUMMARY | cross_file_duplicate_key_count=%d | sample=%s",
            len(cross_dup),
            ",".join(cross_dup[:80]) + ("..." if len(cross_dup) > 80 else ""),
        )
        log.info(
            "CONFIG_RUNTIME_SURFACE_AUDIT | merged_key_count=%d | resolved_snapshot_bytes=%d",
            len(self.config),
            sum(len(k) + len(v) + 2 for k, v in self._resolved_snapshot.items()),
        )
        if not _CONFIG_PROFILE_AUDIT_DONE:
            _CONFIG_PROFILE_AUDIT_DONE = True

    def _audit_cross_file_duplicate_unresolved(self) -> None:
        """Warn when the same key appears in multiple canonical files with different values (Pass 3 manifest policy)."""
        try:
            import importlib.util
            from pathlib import Path as _P

            surf = _P(__file__).resolve().parent / "config_surface_metrics.py"
            spec = importlib.util.spec_from_file_location("_cfg_surface_metrics", surf)
            mod = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(mod)
            manifest_path = self.base_path / "config_manifest.yaml"
            mod.audit_cross_file_duplicates_for_loader(self._key_sources, self.base_path, log, manifest_path)
        except Exception as exc:  # pragma: no cover
            log.debug("CONFIG_CROSS_FILE_DUPLICATE_AUDIT_SKIP | reason=%s", exc)

    def _apply_strategy_mode_preset(self, strategy_mode: str):
        """Apply advanced/quantum overrides (former configs/modes/*.env, May 2026)."""
        if strategy_mode == "standard":
            return
        from modules.strategy_mode_presets import ADVANCED_MODE_PRESET, QUANTUM_MODE_PRESET

        preset = None
        if strategy_mode == "advanced":
            preset = ADVANCED_MODE_PRESET
        elif strategy_mode == "quantum":
            preset = QUANTUM_MODE_PRESET
        if not preset:
            log.warning("No in-repo strategy_mode preset for strategy_mode=%s", strategy_mode)
            return
        tag = "modules/strategy_mode_presets.py"
        for key, value in preset.items():
            sources = self._key_sources.setdefault(key, [])
            if tag not in sources:
                sources.append(tag)
            if len(sources) > 1:
                dup_token = (key, tuple(sources))
                if dup_token not in self._duplicate_keys_logged:
                    log.info(
                        "CONFIG_DUPLICATE_KEY | key=%s | sources=%s",
                        key,
                        sources,
                    )
                    self._duplicate_keys_logged.add(dup_token)
            self.config[key] = value
            self._last_win_path[key] = tag
        if tag not in self.loaded_files:
            self.loaded_files.append(tag)
    
    def _load_secrets(self):
        """
        Load secrets from secretsprivate/ folder for local development.
        Production should use Google Secret Manager instead.
        
        Priority:
        1. Google Secret Manager (production)
        2. secretsprivate/ folder (local development)
        3. Environment variables (fallback)
        """
        # Only load from secretsprivate/ in development/local environments
        if self.config.get("ENVIRONMENT", "").lower() == "production":
            log.info("Production environment: Using Google Secret Manager for secrets")
            return
        
        # Load E*TRADE consumer keys / account ids (local). OAuth access tokens in prod: Secret Manager etrade-oauth-prod.
        etrade_secrets = self.secrets_path / "etrade.env"
        if etrade_secrets.exists():
            log.info(f"Loading E*TRADE secrets from: {etrade_secrets}")
            self._load_env_file(etrade_secrets)
        else:
            log.debug(
                "E*TRADE secrets file not found: %s (use secretsprivate/etrade.env.template → etrade.env; "
                "production: Secret Manager + shell env)",
                etrade_secrets,
            )
        
        # Load Telegram secrets
        telegram_secrets = self.secrets_path / "telegram.env"
        if telegram_secrets.exists():
            log.info(f"Loading Telegram secrets from: {telegram_secrets}")
            self._load_env_file(telegram_secrets)
        else:
            log.debug(f"Telegram secrets file not found: {telegram_secrets} (using Secret Manager or env vars)")
        
        # Also check environment variables as fallback
        # E*TRADE credentials
        if not self.config.get("ETRADE_SANDBOX_KEY") and os.getenv("ETRADE_SANDBOX_KEY"):
            self.config["ETRADE_SANDBOX_KEY"] = os.getenv("ETRADE_SANDBOX_KEY")
        if not self.config.get("ETRADE_SANDBOX_SECRET") and os.getenv("ETRADE_SANDBOX_SECRET"):
            self.config["ETRADE_SANDBOX_SECRET"] = os.getenv("ETRADE_SANDBOX_SECRET")
        if not self.config.get("ETRADE_DEMO_CONSUMER_KEY") and os.getenv("ETRADE_DEMO_CONSUMER_KEY"):
            self.config["ETRADE_DEMO_CONSUMER_KEY"] = os.getenv("ETRADE_DEMO_CONSUMER_KEY")
        if not self.config.get("ETRADE_DEMO_CONSUMER_SECRET") and os.getenv("ETRADE_DEMO_CONSUMER_SECRET"):
            self.config["ETRADE_DEMO_CONSUMER_SECRET"] = os.getenv("ETRADE_DEMO_CONSUMER_SECRET")
        if not self.config.get("ETRADE_PROD_KEY") and os.getenv("ETRADE_PROD_KEY"):
            self.config["ETRADE_PROD_KEY"] = os.getenv("ETRADE_PROD_KEY")
        if not self.config.get("ETRADE_PROD_SECRET") and os.getenv("ETRADE_PROD_SECRET"):
            self.config["ETRADE_PROD_SECRET"] = os.getenv("ETRADE_PROD_SECRET")
        
        # Telegram credentials
        if not self.config.get("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_BOT_TOKEN"):
            self.config["TELEGRAM_BOT_TOKEN"] = os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.config.get("TELEGRAM_CHAT_ID") and os.getenv("TELEGRAM_CHAT_ID"):
            self.config["TELEGRAM_CHAT_ID"] = os.getenv("TELEGRAM_CHAT_ID")
    
    def _validate_configuration(self):
        """Validate required configuration values"""
        required_keys = [
            "STRATEGY_MODE",
            "AUTOMATION_MODE",
            "ENVIRONMENT"
        ]
        
        # Optional but recommended keys
        recommended_keys = [
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID"
        ]
        
        # Add E*TRADE keys only if not in Demo Mode and E*TRADE is enabled
        if (self.config.get("TRADING_MODE", "").upper() != "DEMO_MODE" and 
            self.config.get("ETRADE_ENABLED", "false").lower() == "true"):
            recommended_keys.extend([
                "ETRADE_CONSUMER_KEY",
                "ETRADE_CONSUMER_SECRET"
            ])
        
        missing_keys = []
        for key in required_keys:
            if key not in self.config or not self.config[key]:
                missing_keys.append(key)
        
        if missing_keys:
            log.error(f"Missing required configuration keys: {missing_keys}")
            raise ValueError(f"Missing required configuration: {missing_keys}")
        
        # Check recommended keys
        missing_recommended = []
        for key in recommended_keys:
            if key not in self.config or not self.config[key]:
                missing_recommended.append(key)
        
        if missing_recommended:
            # In Cloud Run, secrets may be loaded via Secret Manager at runtime rather than .env files.
            # Keep this visible but avoid warning-level noise during normal startup.
            if os.getenv("K_SERVICE"):
                log.info(f"Missing recommended configuration keys (cloud startup): {missing_recommended}")
            else:
                log.warning(f"Missing recommended configuration keys: {missing_recommended}")
        
        # Validate strategy mode
        valid_strategies = ["standard", "advanced", "quantum"]
        if self.config["STRATEGY_MODE"] not in valid_strategies:
            raise ValueError(f"Invalid strategy mode: {self.config['STRATEGY_MODE']}")
        
        # Validate automation mode
        valid_automation = ["off", "demo", "live"]
        if self.config["AUTOMATION_MODE"] not in valid_automation:
            raise ValueError(f"Invalid automation mode: {self.config['AUTOMATION_MODE']}")
        
        # Validate environment
        valid_environments = ["development", "production", "sandbox"]
        if self.config["ENVIRONMENT"] not in valid_environments:
            raise ValueError(f"Invalid environment: {self.config['ENVIRONMENT']}")
        
        # Validate data provider priority
        if "DATA_PRIORITY" in self.config:
            providers = self.config["DATA_PRIORITY"].split(",")
            valid_providers = ["etrade", "alpha_vantage", "polygon", "yfinance"]
            for provider in providers:
                if provider.strip() not in valid_providers:
                    log.warning(f"Unknown data provider in DATA_PRIORITY: {provider}")
        
        # Validate broker configuration (multi-broker: etrade, ib, robinhood)
        valid_brokers = ["etrade", "ib", "robinhood"]
        broker_type = (self.config.get("BROKER_TYPE") or "etrade").lower()
        if broker_type not in valid_brokers:
            log.warning(f"BROKER_TYPE={broker_type} not in {valid_brokers}; defaulting to etrade. Set BROKER_TYPE in configs/Data.env")
        
        # Validate numeric ranges
        self._validate_numeric_ranges()
        
        log.info("Configuration validation passed")
    
    def _validate_numeric_ranges(self):
        """Validate numeric configuration values are within reasonable ranges"""
        numeric_validations = {
            "MAX_POSITION_SIZE_PCT": (1.0, 100.0),
            "MIN_POSITION_SIZE_PCT": (0.1, 50.0),
            "RESERVE_CASH_PCT": (5.0, 50.0),
            "PER_TRADE_RISK_CAP_PCT": (1.0, 25.0),
            "PER_TRADE_ALLOC_CAP_PCT": (5.0, 50.0),
            "MAX_DAILY_LOSS_PCT": (1.0, 20.0),
            "STOP_LOSS_ATR_MULTIPLIER": (0.5, 5.0),
            "TAKE_PROFIT_ATR_MULTIPLIER": (1.0, 10.0),
            "POLL_SECONDS": (0.1, 60.0),
            "MAX_WORKERS": (1, 32),
            "CACHE_TTL_SECONDS": (1, 3600)
        }
        
        for key, (min_val, max_val) in numeric_validations.items():
            if key in self.config:
                try:
                    value = float(self.config[key])
                    if value < min_val or value > max_val:
                        log.warning(f"Configuration value {key}={value} is outside recommended range [{min_val}, {max_val}]")
                except (ValueError, TypeError):
                    log.warning(f"Configuration value {key}={self.config[key]} is not a valid number")
    
    def get_config_value(self, key: str, default: Any = None, convert_type: bool = True) -> Any:
        """
        Get configuration value with optional type conversion
        
        Priority order:
        1. Environment variables (os.environ) - highest priority
        2. Config file values (self.config)
        3. Default value
        
        Args:
            key: Configuration key
            default: Default value if key not found
            convert_type: Whether to convert string values to appropriate types
        
        Returns:
            Configuration value
        """
        import os

        in_env = key in os.environ
        legacy_for_canon = (
            key == "ORB_0DTE_OVEREXTENSION_SOFT_THRESHOLD"
            and not in_env
            and "0DTE_EXTENSION_THRESHOLD_PCT" in os.environ
        )
        cache_key = (key, convert_type)
        use_cache = (
            not in_env
            and not legacy_for_canon
            and default is None
            and key in self.config
        )
        if use_cache and cache_key in self._get_value_cache:
            return self._get_value_cache[cache_key]

        if in_env:
            value = os.environ[key]
            if (
                key == "ORB_0DTE_OVEREXTENSION_SOFT_THRESHOLD"
                and "0DTE_EXTENSION_THRESHOLD_PCT" in os.environ
            ):
                try:
                    fc = float(value)
                    fl = float(os.environ["0DTE_EXTENSION_THRESHOLD_PCT"])
                except (TypeError, ValueError):
                    fc, fl = None, None
                if fc is not None and fl is not None and abs(fc - fl) > 1e-12:
                    tkn = "env_both|ORB_0DTE_OVEREXTENSION_SOFT_THRESHOLD|0DTE_EXTENSION_THRESHOLD_PCT"
                    if tkn not in _CONFIG_CANONICAL_OVERRIDE_LOGGED:
                        log.warning(
                            "CONFIG_CANONICAL_KEY_OVERRIDES_ALIAS | canonical_key=%s | alias_key=%s | canonical_value=%s | alias_value=%s",
                            "ORB_0DTE_OVEREXTENSION_SOFT_THRESHOLD",
                            "0DTE_EXTENSION_THRESHOLD_PCT",
                            value,
                            os.environ["0DTE_EXTENSION_THRESHOLD_PCT"],
                        )
                        _CONFIG_CANONICAL_OVERRIDE_LOGGED.add(tkn)
        elif legacy_for_canon:
            value = os.environ["0DTE_EXTENSION_THRESHOLD_PCT"]
            token = "runtime_env|0DTE_EXTENSION_THRESHOLD_PCT->ORB_0DTE_OVEREXTENSION_SOFT_THRESHOLD"
            if token not in _CONFIG_DEPRECATED_ALIAS_LOGGED:
                log.info(
                    "CONFIG_DEPRECATED_ALIAS_USED | old_key=0DTE_EXTENSION_THRESHOLD_PCT | new_key=ORB_0DTE_OVEREXTENSION_SOFT_THRESHOLD",
                )
                _CONFIG_DEPRECATED_ALIAS_LOGGED.add(token)
        else:
            if key in self.config:
                value = self.config[key]
            else:
                value = default
                _log_config_missing_key_once(key)

        if not convert_type or value is None:
            if use_cache:
                if len(self._get_value_cache) >= self._get_value_cache_max:
                    self._get_value_cache.clear()
                self._get_value_cache[cache_key] = value
            return value

        result: Any = value
        if isinstance(value, str):
            if value.lower() in ('true', 'yes', '1', 'on'):
                result = True
            elif value.lower() in ('false', 'no', '0', 'off'):
                result = False
            else:
                try:
                    if '.' in value:
                        result = float(value)
                    else:
                        result = int(value)
                except ValueError:
                    result = value

        if use_cache:
            if len(self._get_value_cache) >= self._get_value_cache_max:
                self._get_value_cache.clear()
            self._get_value_cache[cache_key] = result
        return result
    
    def get_strategy_config(self) -> Dict[str, Any]:
        """Get strategy-specific configuration values"""
        strategy_mode = self.config.get("STRATEGY_MODE", "standard")
        
        prefix = strategy_mode.upper() + "_"
        strategy_config = {}
        
        for key, value in self.config.items():
            if key.startswith(prefix):
                config_key = key[len(prefix):].lower()
                strategy_config[config_key] = self.get_config_value(key)
        
        return strategy_config
    
    def get_automation_config(self) -> Dict[str, Any]:
        """Get automation-specific configuration values"""
        automation_mode = self.config.get("AUTOMATION_MODE", "off")
        
        prefix = automation_mode.upper() + "_"
        automation_config = {}
        
        for key, value in self.config.items():
            if key.startswith(prefix):
                config_key = key[len(prefix):].lower()
                automation_config[config_key] = self.get_config_value(key)
        
        return automation_config
    
    def is_feature_enabled(self, feature: str) -> bool:
        """Check if a feature is enabled"""
        return self.get_config_value(f"{feature.upper()}_ENABLED", False)
    
    def get_performance_mode(self) -> str:
        """Get the current performance mode"""
        return self.config.get("PERFORMANCE_MODE", "standard")
    
    def is_demo_mode(self) -> bool:
        """Check if running in demo mode"""
        return self.config.get("AUTOMATION_MODE") == "demo"
    
    def is_live_mode(self) -> bool:
        """Check if running in live mode"""
        return self.config.get("AUTOMATION_MODE") == "live"
    
    def is_alert_only_mode(self) -> bool:
        """Check if running in alert-only mode"""
        return self.config.get("AUTOMATION_MODE") == "off"
    
    def get_loaded_files(self) -> list:
        """Get list of loaded configuration files"""
        return self.loaded_files.copy()
    
    def export_config(self, filepath: str):
        """Export current configuration to a file"""
        with open(filepath, 'w') as f:
            f.write("# Easy ORB Strategy Configuration Export\n")
            f.write(f"# Generated at: {os.popen('date').read().strip()}\n")
            f.write(f"# Strategy Mode: {self.config.get('STRATEGY_MODE')}\n")
            f.write(f"# Automation Mode: {self.config.get('AUTOMATION_MODE')}\n")
            f.write(f"# Environment: {self.config.get('ENVIRONMENT')}\n\n")
            
            for key, value in sorted(self.config.items()):
                f.write(f"{key}={value}\n")
        
        log.info(f"Configuration exported to: {filepath}")

# Global configuration loader instance
_config_loader = None

def get_config_loader() -> ConfigLoader:
    """Get global configuration loader instance"""
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
    return _config_loader


def reset_config_loader() -> None:
    """Reset the global loader and one-shot diagnostics (tests / forced reload)."""
    global _config_loader, _CONFIG_PROFILE_AUDIT_DONE
    _config_loader = None
    _CONFIG_DEPRECATED_ALIAS_LOGGED.clear()
    _CONFIG_CANONICAL_OVERRIDE_LOGGED.clear()
    _CONFIG_STEALTH_OWNERSHIP_LOGGED.clear()
    _CONFIG_PROFILE_AUDIT_DONE = False

def load_configuration(
    strategy_mode: str = "standard",
    automation_mode: str = "off",
    environment: str = "development"
) -> Dict[str, Any]:
    """Load configuration using global loader"""
    loader = get_config_loader()
    return loader.load_configuration(strategy_mode, automation_mode, environment)

def get_config_value(key: str, default: Any = None, convert_type: bool = True) -> Any:
    """Get configuration value using global loader"""
    loader = get_config_loader()
    return loader.get_config_value(key, default, convert_type)

def is_feature_enabled(feature: str) -> bool:
    """Check if feature is enabled using global loader"""
    loader = get_config_loader()
    return loader.get_config_value(f"ENABLE_{feature.upper()}", False)

def get_cloud_config() -> Dict[str, str]:
    """
    Get cloud configuration values (Rev 00190)
    
    Returns centralized cloud config for easy deployment to different projects.
    All cloud-related values should use this function instead of hardcoded values.
    
    Returns:
        Dict with: project_id, service_name, bucket_name, region, zone
    """
    loader = get_config_loader()
    
    project_id = loader.get_config_value("GCP_PROJECT_ID", "easy-etrade-strategy")
    service_name = loader.get_config_value("GCP_SERVICE_NAME", "easy-etrade-strategy")
    region = loader.get_config_value("GCP_REGION", "us-central1")
    zone = loader.get_config_value("GCP_ZONE", "us-central1-a")
    
    # Auto-derive bucket name from project if not explicitly set
    bucket_name = loader.get_config_value("GCS_BUCKET_NAME", None)
    if not bucket_name:
        bucket_name = f"{project_id}-data"
    
    return {
        "project_id": project_id,
        "service_name": service_name,
        "bucket_name": bucket_name,
        "region": region,
        "zone": zone
    }
