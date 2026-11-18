"""
Views for Queue management, drag-and-drop reordering, and pause controls.
"""
import json
from django.contrib import messages
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
    paused_fb = get_setting("paused_fb", False)

    context = {
        "videos": videos,
        "paused": paused,
        "paused_yt": paused_yt,
        "paused_ig": paused_ig,
        "paused_fb": paused_fb,
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
    """Toggle global or per-platform pause (scope=all|yt|ig|fb)."""
    scope = request.POST.get("scope", "all").lower()
    if scope == "yt":
        curr = get_setting("paused_yt", False)
        new_state = not curr
        set_setting("paused_yt", new_state)
        msg = f"YouTube posting {'paused' if new_state else 'resumed'}."
    elif scope == "ig":
        curr = get_setting("paused_ig", False)
        new_state = not curr
        set_setting("paused_ig", new_state)
        msg = f"Instagram posting {'paused' if new_state else 'resumed'}."
    elif scope == "fb":
        curr = get_setting("paused_fb", False)
        new_state = not curr
        set_setting("paused_fb", new_state)
        msg = f"Facebook posting {'paused' if new_state else 'resumed'}."
    else:
        curr = get_setting("paused", False)
        new_state = not curr
        set_setting("paused", new_state)
        msg = f"Global posting {'paused' if new_state else 'resumed'}."

    messages.info(request, msg)
    if request.headers.get("HX-Request") == "true":
        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": msg, "type": "info"}})
        res["HX-Refresh"] = "true"
        return res
    return redirect("/queue")


@require_POST
def queue_bulk_platform_view(request):
    """
    Bulk platform destination update or post_now for selected videos in Queue.
    POST params:
      - ids[]: list of video UUID strings
      - action: 'enable_platform' | 'disable_platform' | 'set_platforms' | 'post_now'
      - platform: 'yt' | 'fb' | 'ig' | 'all'
      - platforms[]: list of platform strings ['yt', 'fb', 'ig']
    """
    ids = request.POST.getlist("ids[]") or request.POST.getlist("ids")
    action = request.POST.get("action", "").strip()
    platform = request.POST.get("platform", "").lower().strip()
    platforms = request.POST.getlist("platforms[]") or request.POST.getlist("platforms")

    if not ids:
        return JsonResponse({"ok": False, "error": "No videos selected"}, status=400)

    videos = Video.objects.filter(id__in=ids)
    from core.jobs import derive_overall_status

    if action == "post_now":
        from core.scheduling import post_now_video
        from core.jobs import run_uploader
        import threading
        p_count = 0
        for v in videos:
            post_now_video(v)
            p_count += 1
        threading.Thread(target=run_uploader, daemon=True).start()
        messages.success(request, f"Immediate posting triggered for {p_count} video(s)!")
        return JsonResponse({"ok": True, "updated": p_count, "action": "post_now"})

    updated_count = 0
    target_platforms = ["yt", "fb", "ig"] if platform == "all" else [platform] if platform in ("yt", "fb", "ig") else []

    for v in videos:
        modified = False
        if action == "enable_platform":
            for p in target_platforms:
                curr = getattr(v, f"{p}_status")
                if curr not in ("posted", "uploading"):
                    setattr(v, f"{p}_status", "pending")
                    setattr(v, f"{p}_attempts", 0)
                    setattr(v, f"{p}_error", None)
                    setattr(v, f"{p}_next_attempt_at", None)
                    modified = True
        elif action == "disable_platform":
            for p in target_platforms:
                curr = getattr(v, f"{p}_status")
                if curr not in ("posted", "uploading"):
                    setattr(v, f"{p}_status", "skipped")
                    modified = True
        elif action == "set_platforms":
            for p in ["yt", "fb", "ig"]:
                curr = getattr(v, f"{p}_status")
                if curr not in ("posted", "uploading"):
                    if p in platforms:
                        setattr(v, f"{p}_status", "pending")
                        setattr(v, f"{p}_attempts", 0)
                        setattr(v, f"{p}_error", None)
                        setattr(v, f"{p}_next_attempt_at", None)
                    else:
                        setattr(v, f"{p}_status", "skipped")
                    modified = True

        if modified:
            v.save()
            derive_overall_status(v)
            updated_count += 1

    messages.success(request, f"Updated destination platforms for {updated_count} video(s)!")
    return JsonResponse({"ok": True, "updated": updated_count})
