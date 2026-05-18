"""
Shared config surface metrics for report_config_surface.py, ConfigLoader audits, and tests.

Loader merge order (later wins): Data → Shared → ORBSO → ORB0DTE → Trendline0DTE → Risk → Alerts
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Mapping, Optional, Sequence, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

_STRAT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MANIFEST = _STRAT_ROOT / "configs" / "config_manifest.yaml"

CANONICAL_ENV_FILES: Tuple[str, ...] = (
    "Data.env",
    "Shared.env",
    "ORBSO.env",
    "ORB0DTE.env",
    "Trendline0DTE.env",
    "Risk.env",
    "Alerts.env",
)

_EXCLUDED_FROM_PUBLIC_TUNABLE = frozenset(
    {
        "MOVE_TO_CODE_DEFAULT",
        "MERGE_INTO_PROFILE",
        "DEPRECATE",
        "UNKNOWN_USAGE_NEEDS_CODE_SEARCH",
    }
)


def parse_env_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip()
        if "#" in v:
            v = v.split("#")[0].strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        elif v.startswith("'") and v.endswith("'"):
            v = v[1:-1]
        out[k] = v
    return out


def count_physical_assignments(config_dir: Path, names: Sequence[str] = CANONICAL_ENV_FILES) -> int:
    n = 0
    for name in names:
        p = config_dir / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            n += 1
    return n


def merge_canonical_chain(
    config_dir: Path, names: Sequence[str] = CANONICAL_ENV_FILES
) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    merged: Dict[str, str] = {}
    sources: Dict[str, List[str]] = {}
    for name in names:
        part = parse_env_file(config_dir / name)
        for k, v in part.items():
            merged[k] = v
            sources.setdefault(k, []).append(name)
    return merged, sources


def load_manifest(manifest_path: Optional[Path] = None) -> Dict[str, Any]:
    path = manifest_path or _DEFAULT_MANIFEST
    if yaml is None or not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _build_classifier(manifest: Mapping[str, Any]) -> Tuple[Dict[str, Tuple[str, str]], List[Tuple[int, str, str, str]]]:
    """
    Returns:
      exact_map: key -> (group, classification)
      prefix_rows: list of (-len(prefix), prefix, group, classification) for longest-first scan
    """
    exact_map: Dict[str, Tuple[str, str]] = {}
    prefix_rows: List[Tuple[int, str, str, str]] = []
    og = manifest.get("ownership_groups") or {}
    if not isinstance(og, dict):
        return exact_map, prefix_rows
    for group, body in og.items():
        if not isinstance(body, dict):
            continue
        cls = str(body.get("classification") or "UNKNOWN_USAGE_NEEDS_CODE_SEARCH").strip()
        for ek in body.get("exact_keys") or []:
            if isinstance(ek, str) and ek.strip():
                exact_map[ek.strip()] = (str(group), cls)
        for pref in body.get("key_prefixes") or []:
            if not isinstance(pref, str) or not pref.strip():
                continue
            p = pref.strip()
            prefix_rows.append((-len(p), p, str(group), cls))
    prefix_rows.sort(key=lambda r: (r[0], r[1]))
    return exact_map, prefix_rows


def classify_key(
    key: str,
    manifest: Mapping[str, Any],
    exact_map: Optional[Dict[str, Tuple[str, str]]] = None,
    prefix_rows: Optional[List[Tuple[int, str, str, str]]] = None,
) -> Tuple[str, str]:
    """Return (ownership_group, classification)."""
    if exact_map is None or prefix_rows is None:
        exact_map, prefix_rows = _build_classifier(manifest)
    if key in exact_map:
        return exact_map[key]
    for _neg_len, pref, group, cls in prefix_rows:
        p0 = pref.rstrip("_")
        if key.startswith(pref) or key.startswith(p0 + "_") or key == p0:
            return group, cls
    # Built-in fallbacks (when manifest missing pieces)
    if key.startswith("OPTION_STEALTH_") or key.startswith("OPTION_QUOTE_"):
        return "OPTIONS_EXIT", "MERGE_INTO_PROFILE"
    if key.startswith("0DTE_") or key.startswith("ORB_0DTE"):
        return "ORB_0DTE", "MERGE_INTO_PROFILE"
    if key.startswith("TRENDLINE_"):
        return "TRENDLINE_0DTE", "MERGE_INTO_PROFILE"
    if key.startswith("SO_") or key.startswith("ORB_WINDOW") or key.startswith("ORR_"):
        return "ORB_SO", "KEEP_PUBLIC"
    if key.startswith("STEALTH_"):
        return "OPTIONS_EXIT", "MERGE_INTO_PROFILE"
    return "UNKNOWN", "UNKNOWN_USAGE_NEEDS_CODE_SEARCH"


def cross_file_duplicates(sources: Mapping[str, Sequence[str]]) -> List[Tuple[str, List[str]]]:
    return sorted([(k, list(srcs)) for k, srcs in sources.items() if len(srcs) > 1], key=lambda x: x[0])


def duplicate_values_by_file(
    config_dir: Path, key: str, file_names: Sequence[str]
) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name in file_names:
        d = parse_env_file(config_dir / name)
        if key in d:
            out[name] = d[key]
    return out


def compute_surface_report(config_dir: Optional[Path] = None, manifest_path: Optional[Path] = None) -> Dict[str, Any]:
    root = config_dir or (_STRAT_ROOT / "configs")
    manifest = load_manifest(manifest_path)
    merged, sources = merge_canonical_chain(root)
    exact_map, prefix_rows = _build_classifier(manifest)

    physical_lines = count_physical_assignments(root)
    dups = cross_file_duplicates(sources)

    by_group: Dict[str, int] = {}
    by_class: Dict[str, int] = {}
    public_tunable = 0
    unknown_count = 0
    profile_owned_count = 0
    code_default_candidate = 0
    deprecated_candidate = 0

    for k in merged:
        g, cls = classify_key(k, manifest, exact_map, prefix_rows)
        by_group[g] = by_group.get(g, 0) + 1
        by_class[cls] = by_class.get(cls, 0) + 1
        if cls == "UNKNOWN_USAGE_NEEDS_CODE_SEARCH" or g == "UNKNOWN":
            unknown_count += 1
        if cls == "MOVE_TO_CODE_DEFAULT":
            code_default_candidate += 1
        if cls == "DEPRECATE":
            deprecated_candidate += 1
        if cls == "MERGE_INTO_PROFILE":
            profile_owned_count += 1
        if cls not in _EXCLUDED_FROM_PUBLIC_TUNABLE:
            public_tunable += 1
        elif cls == "UNKNOWN_USAGE_NEEDS_CODE_SEARCH" and k in (manifest.get("keep_public_exact_keys") or []):
            public_tunable += 1

    # Prefix group sizes (top 25): first manifest group hit, else UNKNOWN
    prefix_bucket: DefaultDict[str, int] = DefaultDict(int)
    for k in merged:
        g, _cls = classify_key(k, manifest, exact_map, prefix_rows)
        prefix_bucket[g] += 1
    top_prefix_groups = sorted(prefix_bucket.items(), key=lambda x: -x[1])[:25]

    dup_by_owner: Dict[str, int] = DefaultDict(int)
    for key, files in dups:
        vals = duplicate_values_by_file(root, key, files)
        distinct = {str(v).strip() for v in vals.values()}
        g, _cls = classify_key(key, manifest, exact_map, prefix_rows)
        label = f"{g}|same_value={len(distinct) <= 1}"
        dup_by_owner[label] += 1

    policy = manifest.get("cross_file_duplicate_policy") or {}
    allow = set()
    if isinstance(policy.get("resolved_no_warning_keys"), list):
        allow = {str(x).strip() for x in policy["resolved_no_warning_keys"] if str(x).strip()}

    return {
        "total_physical_key_lines": physical_lines,
        "merged_unique_key_count": len(merged),
        "public_tunable_count": public_tunable,
        "profile_owned_count": profile_owned_count,
        "code_default_candidate_count": code_default_candidate,
        "deprecated_candidate_count": deprecated_candidate,
        "unknown_count": unknown_count,
        "cross_file_duplicate_count": len(dups),
        "cross_file_duplicate_keys": [k for k, _ in dups],
        "cross_file_duplicates_by_owner_label": dict(sorted(dup_by_owner.items(), key=lambda x: -x[1])),
        "keys_by_ownership_group": dict(sorted(by_group.items(), key=lambda x: -x[1])),
        "keys_by_classification": dict(sorted(by_class.items(), key=lambda x: -x[1])),
        "top_25_largest_config_prefix_groups": top_prefix_groups,
        "manifest_loaded": bool(manifest),
        "cross_file_duplicate_policy_allowlist_size": len(allow),
    }


def audit_cross_file_duplicates_for_loader(
    key_sources: Mapping[str, Sequence[str]],
    config_dir: Path,
    log,
    manifest_path: Optional[Path] = None,
) -> None:
    """Emit CONFIG_CROSS_FILE_DUPLICATE_UNRESOLVED for keys still duplicated with conflicting values."""
    manifest = load_manifest(manifest_path)
    policy = manifest.get("cross_file_duplicate_policy") or {}
    allow = set()
    if isinstance(policy.get("resolved_no_warning_keys"), list):
        allow = {str(x).strip() for x in policy["resolved_no_warning_keys"] if str(x).strip()}
    exact_map, prefix_rows = _build_classifier(manifest)

    for key, src_paths in key_sources.items():
        if len(src_paths) <= 1:
            continue
        if key in allow:
            continue
        names_u = sorted({Path(p).name for p in src_paths if str(p).endswith(".env")})
        if len(names_u) <= 1:
            continue
        vals = duplicate_values_by_file(config_dir, key, names_u)
        distinct = {str(v).strip() for v in vals.values()}
        if len(distinct) <= 1:
            continue
        g, cls = classify_key(key, manifest, exact_map, prefix_rows)
        log.warning(
            "CONFIG_CROSS_FILE_DUPLICATE_UNRESOLVED | key=%s | ownership_hint=%s | classification=%s | values_by_file=%s",
            key,
            g,
            cls,
            vals,
        )
