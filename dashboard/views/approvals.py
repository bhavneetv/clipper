"""
Views for video approval gate, per-video actions, and approvals live-polling.
"""
from datetime import datetime
import json
import threading
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from core.captions import resolve_facebook_caption, resolve_instagram_caption, resolve_youtube_caption
from core.jobs import run_uploader
from core.models import ActivityLog, Video
from core.scheduling import approve_video, get_now, hold_video, post_now_video, release_video
from core.storage import delete_file


def _enrich_approval_video(v: Video, now: datetime):
    yt_cap, yt_src = resolve_youtube_caption(v)
    ig_cap, ig_src = resolve_instagram_caption(v)
    fb_cap, fb_src = resolve_facebook_caption(v)
    seconds_remaining = 0
    if v.approval_due_at:
        seconds_remaining = max(0, int((v.approval_due_at - now).total_seconds()))

    return {
        "video": v,
        "yt_caption": yt_cap,
        "yt_source": yt_src,
        "ig_caption": ig_cap,
        "ig_source": ig_src,
        "fb_caption": fb_cap,
        "fb_source": fb_src,
        "seconds_remaining": seconds_remaining,
    }


@require_GET
def approvals_view(request):
    """Render approvals page or partial."""
    now = get_now()
    videos = Video.objects.filter(status="awaiting_approval").order_by("approval_due_at", "created_at")
    enriched = [_enrich_approval_video(v, now) for v in videos]

    context = {
        "items": enriched,
        "count": len(enriched),
    }

    if request.headers.get("HX-Request") == "true":
        return render(request, "dashboard/partials/approval_cards.html", context)
    return render(request, "dashboard/approvals.html", context)


@require_POST
def approve_all_view(request):
    """Approve all currently awaiting videos."""
    videos = Video.objects.filter(status="awaiting_approval")
    for v in videos:
        approve_video(v, by="admin")

    if request.headers.get("HX-Request") == "true":
        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"Approved {videos.count()} videos.", "type": "success"}})
        res["HX-Refresh"] = "true"
        return res
    return redirect("/approvals")


@require_POST
def video_approve_view(request, video_id):
    video = get_object_or_404(Video, id=video_id)
    approve_video(video, by="admin")
    if request.headers.get("HX-Request") == "true":
        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"Approved #{video.seq}", "type": "success"}})
        res["HX-Refresh"] = "true"
        return res
    return redirect(request.META.get("HTTP_REFERER", "/approvals"))


@require_POST
def video_hold_view(request, video_id):
    video = get_object_or_404(Video, id=video_id)
    hold_video(video)
    if request.headers.get("HX-Request") == "true":
        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"Held #{video.seq}", "type": "warning"}})
        res["HX-Refresh"] = "true"
        return res
    return redirect(request.META.get("HTTP_REFERER", "/approvals"))


@require_POST
def video_release_view(request, video_id):
    video = get_object_or_404(Video, id=video_id)
    release_video(video)
    if request.headers.get("HX-Request") == "true":
        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"Released and approved #{video.seq}", "type": "success"}})
        res["HX-Refresh"] = "true"
        return res
    return redirect(request.META.get("HTTP_REFERER", "/approvals"))


@require_POST
def video_post_now_view(request, video_id):
    video = get_object_or_404(Video, id=video_id)
    post_now_video(video)
    # Trigger uploader immediately in background thread
    threading.Thread(target=run_uploader, daemon=True).start()
    if request.headers.get("HX-Request") == "true":
        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"Posting #{video.seq} now! Upload started.", "type": "info"}})
        res["HX-Refresh"] = "true"
        return res
    return redirect(request.META.get("HTTP_REFERER", "/approvals"))


@require_POST
def video_skip_view(request, video_id):
    """Skip platform job for video (platform=yt|ig|fb)."""
    video = get_object_or_404(Video, id=video_id)
    platform = request.POST.get("platform", "").lower()
    if platform == "yt":
        video.yt_status = "skipped"
        video.save(update_fields=["yt_status", "updated_at"])
    elif platform == "ig":
        video.ig_status = "skipped"
        video.save(update_fields=["ig_status", "updated_at"])
    elif platform == "fb":
        video.fb_status = "skipped"
        video.save(update_fields=["fb_status", "updated_at"])

    # Update overall status
    from core.jobs import derive_overall_status
    derive_overall_status(video)

    ActivityLog.objects.create(
        video_id=video.id,
        event="skipped",
        platform=platform,
        message=f"Admin skipped {platform.upper()}",
    )
    if request.headers.get("HX-Request") == "true":
        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"Skipped {platform.upper()}", "type": "warning"}})
        res["HX-Refresh"] = "true"
        return res
    return redirect(request.META.get("HTTP_REFERER", "/approvals"))


@require_POST
def video_retry_view(request, video_id):
    """Retry a failed platform job (platform=yt|ig|fb)."""
    video = get_object_or_404(Video, id=video_id)
    platform = request.POST.get("platform", "").lower()
    if platform == "yt":
        video.yt_status = "pending"
        video.yt_attempts = 0
        video.yt_next_attempt_at = None
        video.yt_error = None
        video.save(update_fields=["yt_status", "yt_attempts", "yt_next_attempt_at", "yt_error", "updated_at"])
    elif platform == "ig":
        video.ig_status = "pending"
        video.ig_attempts = 0
        video.ig_next_attempt_at = None
        video.ig_error = None
        video.save(update_fields=["ig_status", "ig_attempts", "ig_next_attempt_at", "ig_error", "updated_at"])
    elif platform == "fb":
        video.fb_status = "pending"
        video.fb_attempts = 0
        video.fb_next_attempt_at = None
        video.fb_error = None
        video.save(update_fields=["fb_status", "fb_attempts", "fb_next_attempt_at", "fb_error", "updated_at"])

    if video.status in ("failed", "posted"):
        video.status = "uploading"
        video.save(update_fields=["status", "updated_at"])

    ActivityLog.objects.create(
        video_id=video.id,
        event="retry",
        platform=platform,
        message=f"Admin retried {platform.upper()}",
    )
    if request.headers.get("HX-Request") == "true":
        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": f"Retrying {platform.upper()}", "type": "info"}})
        res["HX-Refresh"] = "true"
        return res
    return redirect(request.META.get("HTTP_REFERER", f"/videos/{video.id}/"))


@require_POST
def video_delete_view(request, video_id):
    """Delete the stored file now (only if not currently uploading) and mark file_deleted."""
    video = get_object_or_404(Video, id=video_id)
    if video.yt_status == "uploading" or video.ig_status == "uploading" or video.fb_status == "uploading":
        return JsonResponse({"error": "Cannot delete while upload is in progress"}, status=400)

    now = get_now()
    delete_file(video.file_key)
    video.file_deleted_at = now
    video.status = "file_deleted"
    video.save(update_fields=["file_deleted_at", "status", "updated_at"])

    ActivityLog.objects.create(
        video_id=video.id,
        event="deleted",
        message="Stored file deleted by admin.",
    )
    if request.headers.get("HX-Request") == "true":
        res = HttpResponse(status=200)
        res["HX-Trigger"] = json.dumps({"showMessage": {"message": "File deleted.", "type": "info"}})
        res["HX-Refresh"] = "true"
        return res
    return redirect(request.META.get("HTTP_REFERER", "/videos"))


@require_GET
def video_detail_view(request, video_id):
    """Show detailed view of a video, its resolved captions, and activity logs."""
    video = get_object_or_404(Video, id=video_id)
    logs = ActivityLog.objects.filter(video_id=video.id).order_by("-created_at")
    yt_cap, yt_src = resolve_youtube_caption(video)
    ig_cap, ig_src = resolve_instagram_caption(video)
    fb_cap, fb_src = resolve_facebook_caption(video)

    context = {
        "video": video,
        "logs": logs,
        "yt_caption": yt_cap,
        "yt_source": yt_src,
        "ig_caption": ig_cap,
        "ig_source": ig_src,
        "fb_caption": fb_cap,
        "fb_source": fb_src,
    }
    return render(request, "dashboard/video_detail.html", context)


@require_POST
def video_edit_view(request, video_id):
    """Edit captions, post time, and keep flag."""
    video = get_object_or_404(Video, id=video_id)
    video.yt_title = request.POST.get("yt_title", "").strip() or None
    video.yt_description = request.POST.get("yt_description", "").strip() or None
    video.ig_caption = request.POST.get("ig_caption", "").strip() or None
    video.fb_caption = request.POST.get("fb_caption", "").strip() or None

    tags_str = request.POST.get("hashtags", "").strip()
    if tags_str:
        tags = [t.strip().lstrip("#") for t in tags_str.replace(",", " ").split() if t.strip()]
        video.hashtags = tags
    else:
        video.hashtags = None

    custom_post_at = request.POST.get("custom_post_at", "").strip()
    if custom_post_at:
        try:
            # Parse datetime-local e.g. 2026-11-17T14:30
            dt = datetime.fromisoformat(custom_post_at)
            video.custom_post_at = dt
            video.scheduled_at = dt
        except ValueError:
            pass

    video.keep = request.POST.get("keep") in ("true", "1", "on")
    video.save()

    ActivityLog.objects.create(
        video_id=video.id,
        event="edited",
        message="Captions/settings updated by admin.",
    )
    return redirect(f"/videos/{video.id}/")


@require_POST
def videos_bulk_action_view(request):
    """Bulk action on videos: ids[] and action = approve | retry | delete | reschedule."""
    ids = request.POST.getlist("ids[]") or request.POST.getlist("ids")
    action = request.POST.get("action", "").strip()
    videos = Video.objects.filter(id__in=ids)

    if action == "approve":
        for v in videos:
            approve_video(v, by="admin")
    elif action == "delete":
        for v in videos:
            if v.yt_status != "uploading" and v.ig_status != "uploading" and v.fb_status != "uploading":
                delete_file(v.file_key)
                v.file_deleted_at = get_now()
                v.status = "file_deleted"
                v.save()
    elif action == "retry":
        for v in videos:
            v.yt_status = "pending"
            v.yt_attempts = 0
            v.ig_status = "pending"
            v.ig_attempts = 0
            v.fb_status = "pending"
            v.fb_attempts = 0
            v.status = "uploading"
            v.save()

    return redirect("/videos")
