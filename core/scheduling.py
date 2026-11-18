"""
Scheduling and approval gate engine for Shorts Auto-Poster.
Implements slot assignment, approval timeouts, hold/release, queue reordering, and dispatch.
"""
from datetime import datetime, time as dtime, timedelta
import logging
from typing import List, Optional
import zoneinfo
from django.db import transaction
from django.utils import timezone
from core.models import ActivityLog, Video
from core.settings_store import get_setting

logger = logging.getLogger(__name__)

_custom_now: Optional[datetime] = None


def get_now() -> datetime:
    """Return injectable UTC now for test suites and normal execution."""
    global _custom_now
    if _custom_now is not None:
        return _custom_now
    return timezone.now()


def set_fake_now(dt: Optional[datetime]) -> None:
    """Override clock for testing."""
    global _custom_now
    _custom_now = dt


def get_tz() -> zoneinfo.ZoneInfo:
    """Return configured ZoneInfo timezone, fallback to Asia/Kolkata or UTC."""
    tz_str = get_setting("tz", "Asia/Kolkata")
    try:
        return zoneinfo.ZoneInfo(tz_str)
    except Exception:
        return zoneinfo.ZoneInfo("Asia/Kolkata")


def find_next_free_slot(start_from_utc: datetime) -> datetime:
    """
    Find the next available posting time slot starting from start_from_utc.
    Slots are defined by post_times in the local tz timezone.
    A slot is free when no other Video has scheduled_at equal to it.
    """
    tz = get_tz()
    start_local = start_from_utc.astimezone(tz)
    post_times = get_setting("post_times", ["10:00", "14:00", "19:00"])

    # Parse HH:MM times
    parsed_times: List[dtime] = []
    for pt in post_times:
        try:
            parts = pt.strip().split(":")
            parsed_times.append(dtime(int(parts[0]), int(parts[1])))
        except Exception:
            pass

    parsed_times.sort()
    if not parsed_times:
        parsed_times = [dtime(10, 0), dtime(14, 0), dtime(19, 0)]

    current_date = start_local.date()
    # Check slots up to 30 days ahead
    for day_offset in range(30):
        target_date = current_date + timedelta(days=day_offset)
        for t in parsed_times:
            candidate_local = datetime.combine(target_date, t, tzinfo=tz)
            candidate_utc = candidate_local.astimezone(zoneinfo.ZoneInfo("UTC"))

            if candidate_utc <= start_from_utc:
                continue

            # Check if any video already occupies this slot
            if not Video.objects.filter(scheduled_at=candidate_utc).exists():
                return candidate_utc

    # Fallback: 1 day ahead
    return start_from_utc + timedelta(days=1)


def get_first_slot_of_next_day(from_utc: datetime) -> datetime:
    """Find the first configured slot on the day after from_utc in local tz."""
    tz = get_tz()
    from_local = from_utc.astimezone(tz)
    next_date = from_local.date() + timedelta(days=1)
    post_times = get_setting("post_times", ["10:00", "14:00", "19:00"])
    t_obj = dtime(10, 0)
    if post_times:
        try:
            parts = post_times[0].strip().split(":")
            t_obj = dtime(int(parts[0]), int(parts[1]))
        except Exception:
            pass
    dt_local = datetime.combine(next_date, t_obj, tzinfo=tz)
    return dt_local.astimezone(zoneinfo.ZoneInfo("UTC"))


def run_tick():
    """
    Periodic tick (every 1 minute):
    a) Assign slots
    b) Open approval gate
    c) Auto-approve
    d) Dispatch
    """
    now = get_now()
    paused = get_setting("paused", False)
    approval_timeout_min = int(get_setting("approval_timeout_min", 10))

    # --- Step a: Assign slots ---
    queued_videos = Video.objects.filter(status="queued", scheduled_at__isnull=True).order_by("queue_position", "created_at")
    for v in queued_videos:
        try:
            if v.post_now:
                v.scheduled_at = now
            elif v.custom_post_at:
                v.scheduled_at = v.custom_post_at
            else:
                v.scheduled_at = find_next_free_slot(now)
            v.status = "scheduled"
            v.save(update_fields=["scheduled_at", "status", "updated_at"])
            ActivityLog.objects.create(
                video_id=v.id,
                event="scheduled",
                message=f"Scheduled {v.file_name} for {v.scheduled_at.isoformat()}",
            )
        except Exception as e:
            logger.error("Error assigning slot for video %s: %s", v.id, e)

    # --- Step b: Open approval gate ---
    scheduled_videos = Video.objects.filter(status="scheduled", scheduled_at__lte=now)
    for v in scheduled_videos:
        try:
            if v.post_now or approval_timeout_min == 0:
                v.approved_at = now
                v.approved_by = "admin" if v.post_now else "auto"
                v.status = "uploading"
                v.save(update_fields=["approved_at", "approved_by", "status", "updated_at"])
                ActivityLog.objects.create(
                    video_id=v.id,
                    event="approved",
                    message=f"Instantly approved {v.file_name} (by: {v.approved_by})",
                )
            else:
                # If system was asleep and scheduled_at is far in the past, countdown starts from now
                if (now - v.scheduled_at).total_seconds() > (approval_timeout_min * 60):
                    start_time = now
                else:
                    start_time = v.scheduled_at
                v.status = "awaiting_approval"
                v.approval_due_at = start_time + timedelta(minutes=approval_timeout_min)
                v.save(update_fields=["status", "approval_due_at", "updated_at"])
                ActivityLog.objects.create(
                    video_id=v.id,
                    event="awaiting_approval",
                    message=f"Approval gate opened for {v.file_name}, due at {v.approval_due_at.isoformat()}",
                )
        except Exception as e:
            logger.error("Error opening gate for video %s: %s", v.id, e)

    # --- Step c: Auto-approve ---
    if not paused:
        awaiting_videos = Video.objects.filter(status="awaiting_approval", held=False, approval_due_at__lte=now)
        for v in awaiting_videos:
            try:
                v.approved_at = now
                v.approved_by = "auto"
                v.status = "uploading"
                v.save(update_fields=["approved_at", "approved_by", "status", "updated_at"])
                ActivityLog.objects.create(
                    video_id=v.id,
                    event="approved",
                    message=f"Auto-approved {v.file_name} after countdown expiration.",
                )
            except Exception as e:
                logger.error("Error auto-approving video %s: %s", v.id, e)

    # --- Step d: Dispatch ---
    # Approved videos dispatch to platform queues
    paused_yt = get_setting("paused_yt", False)
    paused_ig = get_setting("paused_ig", False)
    paused_fb = get_setting("paused_fb", False)
    max_yt = int(get_setting("max_per_day_yt", 6))
    max_ig = int(get_setting("max_per_day_ig", 10))
    max_fb = int(get_setting("max_per_day_fb", 10))

    tz = get_tz()
    today_local = now.astimezone(tz).date()

    # Calculate posts today for YouTube
    yt_count_today = Video.objects.filter(
        yt_posted_at__date=today_local,
        yt_status="posted"
    ).count()

    # Calculate posts today for Instagram
    ig_count_today = Video.objects.filter(
        ig_posted_at__date=today_local,
        ig_status="posted"
    ).count()

    # Calculate posts today for Facebook
    fb_count_today = Video.objects.filter(
        fb_posted_at__date=today_local,
        fb_status="posted"
    ).count()

    approved_videos = Video.objects.filter(
        approved_at__isnull=False,
        status="uploading"
    )

    for v in approved_videos:
        update_fields = []
        # Check YouTube daily cap and pause
        if v.yt_status == "pending":
            if paused or paused_yt:
                pass
            elif yt_count_today >= max_yt:
                # Defer to first slot of next day
                next_slot = get_first_slot_of_next_day(now)
                v.yt_next_attempt_at = next_slot
                update_fields.append("yt_next_attempt_at")
                ActivityLog.objects.create(
                    video_id=v.id,
                    event="scheduled",
                    platform="youtube",
                    message=f"YouTube daily cap reached ({max_yt}). Deferred to {next_slot.isoformat()}",
                )

        # Check Instagram daily cap and pause
        if v.ig_status == "pending":
            if paused or paused_ig:
                pass
            elif ig_count_today >= max_ig:
                next_slot = get_first_slot_of_next_day(now)
                v.ig_next_attempt_at = next_slot
                update_fields.append("ig_next_attempt_at")
                ActivityLog.objects.create(
                    video_id=v.id,
                    event="scheduled",
                    platform="instagram",
                    message=f"Instagram daily cap reached ({max_ig}). Deferred to {next_slot.isoformat()}",
                )

        # Check Facebook daily cap and pause
        if v.fb_status == "pending":
            if paused or paused_fb:
                pass
            elif fb_count_today >= max_fb:
                next_slot = get_first_slot_of_next_day(now)
                v.fb_next_attempt_at = next_slot
                update_fields.append("fb_next_attempt_at")
                ActivityLog.objects.create(
                    video_id=v.id,
                    event="scheduled",
                    platform="facebook",
                    message=f"Facebook daily cap reached ({max_fb}). Deferred to {next_slot.isoformat()}",
                )

        if update_fields:
            update_fields.append("updated_at")
            v.save(update_fields=update_fields)


def reorder_queue(video_ids: List[str]) -> None:
    """
    Atomically reorder queue positions and reset scheduled_at for upcoming videos.
    """
    with transaction.atomic():
        for pos, vid_id in enumerate(video_ids, start=1):
            Video.objects.filter(id=vid_id).update(queue_position=pos)

        # Clear scheduled_at and set status=queued for videos with status=scheduled and custom_post_at is null
        Video.objects.filter(
            id__in=video_ids,
            status="scheduled",
            custom_post_at__isnull=True,
        ).update(status="queued", scheduled_at=None)


def approve_video(video: Video, by: str = "admin") -> None:
    """Approve video immediately."""
    now = get_now()
    video.approved_at = now
    video.approved_by = by
    video.status = "uploading"
    video.held = False
    video.save(update_fields=["approved_at", "approved_by", "status", "held", "updated_at"])
    ActivityLog.objects.create(
        video_id=video.id,
        event="approved",
        message=f"Approved by {by}",
    )


def hold_video(video: Video) -> None:
    """Place video on administrative hold."""
    video.held = True
    video.save(update_fields=["held", "updated_at"])
    ActivityLog.objects.create(
        video_id=video.id,
        event="held",
        message="Video placed on hold by admin.",
    )


def release_video(video: Video) -> None:
    """Release video from hold and immediately approve."""
    video.held = False
    now = get_now()
    video.approved_at = now
    video.approved_by = "admin"
    video.status = "uploading"
    video.save(update_fields=["held", "approved_at", "approved_by", "status", "updated_at"])
    ActivityLog.objects.create(
        video_id=video.id,
        event="released",
        message="Video released and approved by admin.",
    )


def post_now_video(video: Video) -> None:
    """Set post_now=True, schedule for now, and approve immediately."""
    now = get_now()
    video.post_now = True
    video.scheduled_at = now
    video.approved_at = now
    video.approved_by = "admin"
    video.status = "uploading"
    video.held = False
    video.save(update_fields=["post_now", "scheduled_at", "approved_at", "approved_by", "status", "held", "updated_at"])
    ActivityLog.objects.create(
        video_id=video.id,
        event="approved",
        message="Post Now triggered by admin.",
    )
