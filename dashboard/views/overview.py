"""
Views for Overview dashboard page and live summary cards fragment.
"""
from datetime import datetime, timedelta
import zoneinfo
from django.db.models import Q, Sum
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET
from core.models import Account, ActivityLog, Video
from core.scheduling import get_now, get_tz
from core.settings_store import get_all_settings, get_setting


def get_overview_context(is_superadmin=True):
    now = get_now()
    tz = get_tz()
    now_local = now.astimezone(tz)
    today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(zoneinfo.ZoneInfo("UTC"))
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)

    # 1. Post statistics
    posted_today_total = Video.objects.filter(status="posted", updated_at__gte=today_start).count()
    posted_today_yt = Video.objects.filter(yt_status="posted", yt_posted_at__gte=today_start).count()
    posted_today_ig = Video.objects.filter(ig_status="posted", ig_posted_at__gte=today_start).count()
    posted_today_fb = Video.objects.filter(fb_status="posted", fb_posted_at__gte=today_start).count()

    posted_week_total = Video.objects.filter(status="posted", updated_at__gte=week_start).count()
    posted_month_total = Video.objects.filter(status="posted", updated_at__gte=month_start).count()
    posted_all_total = Video.objects.filter(status__in=["posted", "file_deleted"]).count()

    # Queue stats
    in_queue_count = Video.objects.filter(status__in=["queued", "scheduled"]).count()
    awaiting_count = Video.objects.filter(status="awaiting_approval").count()
    failed_count = Video.objects.filter(status="failed").count()

    # Storage statistics
    limit_gb = float(get_setting("storage_limit_gb", 1))
    limit_bytes = limit_gb * 1024 * 1024 * 1024
    used_bytes = Video.objects.filter(file_deleted_at__isnull=True).aggregate(
        Sum("size_bytes")
    )["size_bytes__sum"] or 0
    used_gb = round(used_bytes / (1024 * 1024 * 1024), 2)
    free_gb = max(0.0, round(limit_gb - used_gb, 2))
    storage_percent = round((used_bytes / limit_bytes) * 100, 1) if limit_bytes > 0 else 0
    storage_warn = storage_percent >= 80

    # Next 5 scheduled posts
    next_posts = Video.objects.filter(
        status__in=["queued", "scheduled"],
        scheduled_at__isnull=False,
    ).order_by("scheduled_at")[:5]

    # Platform health
    yt_acc = Account.objects.filter(platform="youtube").first()
    ig_acc = Account.objects.filter(platform="instagram").first()
    fb_acc = Account.objects.filter(platform="facebook").first()
    yt_quota_used = posted_today_yt * 1600
    yt_quota_total = 10000

    # Warnings
    warnings = []
    default_cap = get_setting("default_caption") or {}
    if not default_cap.get("title") and not default_cap.get("description"):
        warnings.append({
            "type": "caption",
            "message": "Default caption is empty. You must configure default captions before posting.",
            "link": "/settings",
            "action": "Configure Captions",
        })

    if is_superadmin:
        if not yt_acc or yt_acc.status != "connected":
            warnings.append({
                "type": "account",
                "message": "YouTube account is disconnected. Connect your Google account to enable Shorts.",
                "link": "/accounts",
                "action": "Connect YouTube",
            })

        if not ig_acc or ig_acc.status != "connected":
            warnings.append({
                "type": "account",
                "message": "Instagram account is disconnected. Connect your Instagram account to enable Reels.",
                "link": "/accounts",
                "action": "Connect Instagram",
            })

    notify_enabled = get_setting("notify_enabled", True)
    notify_email = get_setting("notify_email", "").strip()
    if notify_enabled and not notify_email:
        warnings.append({
            "type": "notification",
            "message": "Notifications are enabled, but no recipient email is configured.",
            "link": "/settings",
            "action": "Set Email",
        })

    return {
        "posted_today_total": posted_today_total,
        "posted_today_yt": posted_today_yt,
        "posted_today_ig": posted_today_ig,
        "posted_today_fb": posted_today_fb,
        "posted_week_total": posted_week_total,
        "posted_month_total": posted_month_total,
        "posted_all_total": posted_all_total,
        "in_queue_count": in_queue_count,
        "awaiting_count": awaiting_count,
        "failed_count": failed_count,
        "limit_gb": limit_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "storage_percent": storage_percent,
        "storage_warn": storage_warn,
        "next_posts": next_posts,
        "yt_acc": yt_acc,
        "ig_acc": ig_acc,
        "fb_acc": fb_acc,
        "yt_quota_used": yt_quota_used,
        "yt_quota_total": yt_quota_total,
        "warnings": warnings,
    }


@require_GET
def overview_view(request):
    """Full overview page."""
    is_super = request.session.get("user_role") == "super"
    context = get_overview_context(is_superadmin=is_super)
    return render(request, "dashboard/overview.html", context)


@require_GET
def summary_partial_view(request):
    """HTMX polled partial for overview cards."""
    is_super = request.session.get("user_role") == "super"
    context = get_overview_context(is_superadmin=is_super)
    return render(request, "dashboard/partials/summary_cards.html", context)
