"""
Views for Queue management, drag-and-drop reordering, and pause controls.
"""
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST
from core.models import Video
from core.scheduling import reorder_queue
from core.settings_store import get_setting, set_setting


@require_GET
def queue_view(request):
    """Upcoming queued and scheduled videos in order."""
    videos = Video.objects.filter(
        status__in=["queued", "scheduled"]
    ).order_by("queue_position", "created_at")

    paused = get_setting("paused", False)
    paused_yt = get_setting("paused_yt", False)
    paused_ig = get_setting("paused_ig", False)

    context = {
        "videos": videos,
        "paused": paused,
        "paused_yt": paused_yt,
        "paused_ig": paused_ig,
    }
    return render(request, "dashboard/queue.html", context)


@require_POST
def queue_reorder_view(request):
    """
    Handle drag-and-drop reorder from SortableJS.
    Receives ids[] in the new order.
    """
    ids = request.POST.getlist("ids[]") or request.POST.getlist("ids")
    if ids:
        reorder_queue(ids)
    return JsonResponse({"ok": True})


@require_POST
def queue_pause_view(request):
    """Toggle global or per-platform pause (scope=all|yt|ig)."""
    scope = request.POST.get("scope", "all").lower()
    if scope == "yt":
        curr = get_setting("paused_yt", False)
        set_setting("paused_yt", not curr)
    elif scope == "ig":
        curr = get_setting("paused_ig", False)
        set_setting("paused_ig", not curr)
    elif scope == "fb":
        curr = get_setting("paused_fb", False)
        set_setting("paused_fb", not curr)
    else:
        curr = get_setting("paused", False)
        set_setting("paused", not curr)

    if request.headers.get("HX-Request") == "true":
        res = HttpResponse(status=200)
        res["HX-Refresh"] = "true"
        return res
    return redirect("/queue")
