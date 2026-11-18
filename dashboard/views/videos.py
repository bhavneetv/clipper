"""
Views for Videos list, video details, video actions, and storage scanning.
"""
import logging
import os
from datetime import datetime
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from core.jobs import run_watcher
from core.models import ActivityLog, Video
from core.storage import create_signed_upload_url, delete_file, get_local_inbox_dir, get_storage_backend

logger = logging.getLogger(__name__)


@require_GET
def videos_list_view(request):
    """
    Displays paginated list of videos with filters:
    status, platform, from, to, q (search).
    Returns partial for HTMX requests or full template.
    """
    qs = Video.objects.all().order_by("-created_at")

    status_filter = request.GET.get("status", "").strip()
    platform_filter = request.GET.get("platform", "").strip()
    q_search = request.GET.get("q", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    if status_filter:
        qs = qs.filter(status=status_filter)

    if platform_filter == "youtube":
        qs = qs.filter(Q(yt_status="posted") | Q(yt_status="uploading") | Q(yt_status="failed"))
    elif platform_filter == "instagram":
        qs = qs.filter(Q(ig_status="posted") | Q(ig_status="uploading") | Q(ig_status="failed"))
    elif platform_filter == "facebook":
        qs = qs.filter(Q(fb_status="posted") | Q(fb_status="uploading") | Q(fb_status="failed"))

    if q_search:
        qs = qs.filter(
            Q(file_name__icontains=q_search)
            | Q(yt_title__icontains=q_search)
            | Q(yt_description__icontains=q_search)
            | Q(ig_caption__icontains=q_search)
            | Q(fb_caption__icontains=q_search)
        )

    if date_from:
        try:
            df = datetime.strptime(date_from, "%Y-%m-%d")
            qs = qs.filter(created_at__date__gte=df.date())
        except ValueError:
            pass

    if date_to:
        try:
            dt = datetime.strptime(date_to, "%Y-%m-%d")
            qs = qs.filter(created_at__date__lte=dt.date())
        except ValueError:
            pass

    paginator = Paginator(qs, 20)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "videos": page_obj.object_list,
        "status_filter": status_filter,
        "platform_filter": platform_filter,
        "q_search": q_search,
        "date_from": date_from,
        "date_to": date_to,
        "STATUS_CHOICES": Video.STATUS_CHOICES,
        "storage_backend": get_storage_backend(),
    }

    if request.headers.get("HX-Request") == "true":
        return render(request, "dashboard/partials/video_rows.html", context)

    return render(request, "dashboard/videos.html", context)


@require_POST
def scan_inbox_view(request):
    """Trigger manual scan and sync of configured inbox storage."""
    try:
        count = run_watcher()
        if count > 0:
            messages.success(request, f"Successfully synced and imported {count} new video(s) from storage!")
        else:
            messages.info(request, "Storage sync complete: No new video files found.")
    except Exception as e:
        logger.error("Error in scan_inbox_view: %s", e, exc_info=True)
        messages.error(request, f"Storage sync error: {str(e)}")

    if request.headers.get("HX-Request") == "true":
        response = HttpResponse(status=200)
        response["HX-Refresh"] = "true"
        return response
    referer = request.META.get("HTTP_REFERER") or "/videos"
    return redirect(referer)


@require_POST
def presign_upload_view(request):
    """Generate signed upload URL or parameters for browser upload."""
    file_name = request.POST.get("file_name", "").strip()
    if not file_name:
        return JsonResponse({"error": "file_name is required"}, status=400)
    if not file_name.lower().endswith(".mp4"):
        return JsonResponse({"error": "Only .mp4 files are accepted"}, status=400)

    try:
        data = create_signed_upload_url(file_name)
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def local_video_media_view(request, filename):
    """Serve a local inbox video with HTTP 206 Partial Content range support for browser preview."""
    clean_name = os.path.basename(filename)
    filepath = get_local_inbox_dir() / clean_name
    if not filepath.exists() or not filepath.is_file():
        raise Http404("Video file not found.")
    return FileResponse(open(filepath, "rb"), content_type="video/mp4")


@require_POST
def local_video_upload_view(request):
    """Receive an uploaded video and write it directly to the local inbox directory."""
    uploaded_file = request.FILES.get("file")
    file_name = request.POST.get("file_name", "") or (uploaded_file.name if uploaded_file else "")
    file_name = os.path.basename(file_name).strip()

    if not file_name or not file_name.lower().endswith(".mp4"):
        return JsonResponse({"error": "Only .mp4 files are accepted."}, status=400)

    inbox_dir = get_local_inbox_dir()
    target_path = inbox_dir / file_name

    try:
        if uploaded_file:
            with open(target_path, "wb") as dest:
                for chunk in uploaded_file.chunks(chunk_size=1024 * 1024):
                    dest.write(chunk)
        else:
            with open(target_path, "wb") as dest:
                dest.write(request.body)

        return JsonResponse({"success": True, "file_name": file_name, "path": str(target_path)})
    except Exception as e:
        logger.error("Failed to write local video file: %s", e)
        return JsonResponse({"error": str(e)}, status=500)
