"""
Views for Activity Logs inspection and filtering.
"""
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.views.decorators.http import require_GET
from core.models import ActivityLog


@require_GET
def logs_view(request):
    """Paginated activity log list with event/platform filters."""
    qs = ActivityLog.objects.all().order_by("-created_at")

    event_filter = request.GET.get("event", "").strip()
    platform_filter = request.GET.get("platform", "").strip()
    q_search = request.GET.get("q", "").strip()

    if event_filter:
        qs = qs.filter(event=event_filter)

    if platform_filter:
        qs = qs.filter(platform=platform_filter)

    if q_search:
        qs = qs.filter(Q(message__icontains=q_search) | Q(event__icontains=q_search))

    paginator = Paginator(qs, 30)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    # Distinct events for dropdown
    distinct_events = [
        "detected", "invalid", "scheduled", "awaiting_approval",
        "approved", "held", "released", "uploading", "posted",
        "failed", "retry", "deleted", "email_sent", "email_failed",
        "token_refreshed", "warning", "error",
    ]

    context = {
        "page_obj": page_obj,
        "logs": page_obj.object_list,
        "event_filter": event_filter,
        "platform_filter": platform_filter,
        "q_search": q_search,
        "events": distinct_events,
    }
    return render(request, "dashboard/logs.html", context)
