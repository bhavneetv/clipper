from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def health_check(request):
    """
    Public health check endpoint for keep-alive cron and uptime monitors.
    Returns {"ok": true} without querying the database.
    """
    return JsonResponse({"ok": True})
