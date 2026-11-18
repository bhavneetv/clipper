"""
Cron trigger endpoint for external scheduling services (e.g. cron-job.org, Upstash, GitHub Actions).
Pings wake up sleeping server, run inbox detection, check approval timeouts, and dispatch scheduled posts.
"""
import logging
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from core.jobs import run_tick, run_uploader, run_watcher
from core.settings_store import get_setting

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def cron_trigger_view(request):
    """
    Publicly pingable cron endpoint.
    Protected by ?key=<CRON_SECRET> or Authorization: Bearer <CRON_SECRET>.
    """
    expected_secret = getattr(settings, "CRON_SECRET", None) or get_setting("cron_secret", "shorts_cron_key_123456")
    provided_secret = request.GET.get("key") or request.GET.get("token")

    if not provided_secret:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            provided_secret = auth_header[7:].strip()

    if expected_secret and provided_secret != expected_secret:
        return JsonResponse({"error": "Unauthorized. Invalid or missing cron key parameter (?key=...)."}, status=401)

    actions = []

    # 1. Scan inbox for new videos
    try:
        run_watcher()
        actions.append("watcher_scanned")
    except Exception as e:
        logger.error("Cron watcher error: %s", e)
        actions.append(f"watcher_error: {str(e)}")

    # 2. Process scheduler ticks (auto-approvals & slot assignment)
    try:
        run_tick()
        actions.append("tick_processed")
    except Exception as e:
        logger.error("Cron tick error: %s", e)
        actions.append(f"tick_error: {str(e)}")

    # 3. Dispatch uploads whose scheduled time has arrived
    try:
        run_uploader()
        actions.append("uploader_dispatched")
    except Exception as e:
        logger.error("Cron uploader error: %s", e)
        actions.append(f"uploader_error: {str(e)}")

    return JsonResponse({
        "ok": True,
        "message": "Cron execution completed successfully.",
        "timestamp": timezone.now().isoformat(),
        "actions": actions,
    })
