"""
Cloud Run revision cleanup via Python API (no gcloud required).
Rev 00294: Enables cleanup endpoint to work inside Cloud Run container.
"""
from typing import List, Tuple
import logging

log = logging.getLogger(__name__)

PROJECT_ID = "easy-etrade-strategy"
REGION = "us-central1"
SERVICES = ["easy-etrade-strategy", "easy-collector", "easy-etrade-strategy-oauth"]
KEEP_REVISIONS = 20


def cleanup_cloud_run_revisions() -> dict:
    """
    Delete old Cloud Run revisions, keeping the latest KEEP_REVISIONS per service.
    Uses Cloud Run Admin API - works inside container without gcloud.
    Returns dict with deleted counts and any errors.
    """
    results = {"deleted": 0, "kept": 0, "errors": [], "per_service": {}}
    try:
        from google.cloud.run_v2 import RevisionsClient
        from google.cloud.run_v2.types import ListRevisionsRequest, DeleteRevisionRequest
        from google.api_core import exceptions as google_exceptions
    except ImportError as e:
        results["errors"].append(f"google-cloud-run not installed: {e}")
        return results

    client = RevisionsClient()
    parent_base = f"projects/{PROJECT_ID}/locations/{REGION}"

    for service in SERVICES:
        parent = f"{parent_base}/services/{service}"
        try:
            deleted, kept, errs = _cleanup_service_revisions(
                client, parent, service, ListRevisionsRequest, DeleteRevisionRequest, google_exceptions
            )
            results["deleted"] += deleted
            results["kept"] += kept
            results["errors"].extend(errs)
            results["per_service"][service] = {"deleted": deleted, "kept": kept}
        except google_exceptions.NotFound:
            log.info(f"   {service}: service not found (skipped)")
            results["per_service"][service] = {"deleted": 0, "kept": 0, "skipped": "service not found"}

    return results


def _cleanup_service_revisions(
    client, parent: str, service_name: str,
    ListRevisionsRequest, DeleteRevisionRequest, google_exceptions
) -> Tuple[int, int, List[str]]:
    deleted, kept = 0, 0
    errors = []
    try:
        request = ListRevisionsRequest(parent=parent, page_size=100)
        revisions = list(client.list_revisions(request=request))
        def _sort_key(r):
            if not r.create_time:
                return 0
            return getattr(r.create_time, 'seconds', 0) or 0
        revisions.sort(key=_sort_key, reverse=True)

        total = len(revisions)
        log.info(f"   {service_name}: {total} revisions, keeping latest {KEEP_REVISIONS}")

        for i, rev in enumerate(revisions):
            if i < KEEP_REVISIONS:
                kept += 1
                continue
            rev_name = rev.name
            if not rev_name:
                continue
            try:
                op = client.delete_revision(request=DeleteRevisionRequest(name=rev_name))
                op.result(timeout=30)
                deleted += 1
                log.info(f"   🗑️ Deleted: {rev_name.split('/')[-1]}")
            except google_exceptions.PermissionDenied as e:
                errors.append(f"{service_name}: Permission denied for {rev_name}: {e}")
                kept += 1
            except Exception as e:
                errors.append(f"{service_name}: Failed to delete {rev_name}: {e}")
                kept += 1
    except google_exceptions.NotFound:
        raise  # Propagate so caller can skip service
    except Exception as e:
        errors.append(f"{service_name}: List failed: {e}")

    return deleted, kept, errors
