"""
Scheduler jobs for Shorts Auto-Poster using APScheduler.
Includes watcher, tick, uploader, cleanup, token_refresh, and storage_check.
"""
import logging
import os
import re
import sqlite3
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from django.conf import settings
from django.db import close_old_connections, models, transaction
from datetime import timedelta
import threading
from core.facebook import publish_video_to_facebook
from core.instagram import publish_reel_to_instagram, refresh_instagram_token
from core.models import Account, ActivityLog, Video
from core.notifier import notify_post_failure, notify_post_success
from core.scheduling import get_now, run_tick
from core.settings_store import get_setting
from core.storage import delete_file, get_public_url, list_inbox_videos
from core.video_probe import probe_video
from core.youtube import get_valid_youtube_access_token, upload_video_to_youtube

logger = logging.getLogger(__name__)

scheduler = None
_uploader_lock = threading.Lock()


def reset_stuck_uploads():
    """
    On startup, reset any platform job stuck in 'uploading' for longer than 30 minutes
    back to 'pending', but only if *_video_id / *_media_id is empty.
    """
    now = get_now()
    threshold = now - timedelta(minutes=30)
    Video.objects.filter(
        yt_status="uploading",
        yt_video_id__isnull=True,
        updated_at__lte=threshold,
    ).update(yt_status="pending")

    Video.objects.filter(
        ig_status="uploading",
        ig_media_id__isnull=True,
        updated_at__lte=threshold,
    ).update(ig_status="pending")

    Video.objects.filter(
        fb_status="uploading",
        fb_video_id__isnull=True,
        updated_at__lte=threshold,
    ).update(fb_status="pending")


def run_job_safely(name, func, *args, **kwargs):
    """Wrapper to close old DB connections before and after job execution."""
    close_old_connections()
    try:
        func(*args, **kwargs)
    except Exception as e:
        logger.exception("Error in job %s: %s", name, e)
        try:
            ActivityLog.objects.create(
                event="error",
                message=f"Error in job {name}: {str(e)}",
            )
        except Exception:
            pass
    finally:
        close_old_connections()


def run_watcher() -> int:
    """
    Scans storage inbox for new .mp4 files, probes them with ffprobe,
    detects duplicates, creates Video and ActivityLog records, and assigns
    intelligent titles for video-cutter parts.
    Returns the count of newly detected and queued videos.
    """
    inbox_items = list_inbox_videos()
    if not inbox_items:
        return 0

    imported_count = 0
    for item in inbox_items:
        key = item["key"]
        name = item["name"]
        size = item["size"]
        etag = item.get("etag") or ""

        # Ignore if file_key already in database
        if Video.objects.filter(file_key=key).exists():
            continue

        with transaction.atomic():
            # Double check inside transaction
            if Video.objects.filter(file_key=key).exists():
                continue

            max_pos = Video.objects.aggregate(models.Max("queue_position"))["queue_position__max"] or 0
            next_pos = max_pos + 1
            pub_url = get_public_url(key)

            # Check duplicate hash against valid/posted/queued videos
            if etag and Video.objects.filter(file_hash=etag).exclude(status="invalid").exists():
                v = Video.objects.create(
                    file_key=key,
                    file_name=name,
                    public_url=pub_url,
                    file_hash=etag,
                    size_bytes=size,
                    status="invalid",
                    invalid_reason="duplicate",
                    queue_position=next_pos,
                )
                ActivityLog.objects.create(
                    video_id=v.id,
                    event="invalid",
                    message=f"Duplicate file detected ({name}, hash: {etag})",
                )
                continue

            # Probe video using ffprobe (supports local files and remote URLs)
            try:
                is_valid, reason, meta = probe_video(pub_url, file_size_bytes=size, file_key=key)
            except TypeError:
                is_valid, reason, meta = probe_video(pub_url, file_size_bytes=size)

            # Smart title resolution for video-cutter clips
            resolved_title = None
            job_match = re.search(r"job_([a-f0-9\-]+)", name, re.IGNORECASE)
            part_match = re.search(r"part_(\d+)", name, re.IGNORECASE)
            part_num = part_match.group(1) if part_match else None

            if job_match:
                job_id = job_match.group(1)
                # Check local video-cutter database if available
                for db_path in [
                    r"Y:\projects\video-cutter\backend\storage\app.db",
                    r"Y:\projects\video-cutter\storage\app.db",
                ]:
                    if os.path.exists(db_path):
                        try:
                            conn = sqlite3.connect(db_path)
                            cur = conn.cursor()
                            cur.execute("SELECT v.title FROM jobs j JOIN videos v ON j.video_id = v.id WHERE j.id = ?", (job_id,))
                            row = cur.fetchone()
                            if row and row[0]:
                                base_t = row[0].split("|")[0].strip()
                                resolved_title = f"{base_t} - Part {part_num}" if part_num else base_t
                                break
                        except Exception:
                            pass
                if not resolved_title and part_num:
                    resolved_title = f"Clip Part {part_num}"

            if is_valid:
                v = Video.objects.create(
                    file_key=key,
                    file_name=name,
                    public_url=pub_url,
                    file_hash=etag,
                    size_bytes=meta.get("size_bytes") or size,
                    duration_s=meta.get("duration_s"),
                    width=meta.get("width"),
                    height=meta.get("height"),
                    status="queued",
                    queue_position=next_pos,
                    yt_title=resolved_title or "",
                    fb_caption=resolved_title or "",
                    ig_caption=resolved_title or "",
                )
                imported_count += 1
                ActivityLog.objects.create(
                    video_id=v.id,
                    event="detected",
                    message=f"Synced and validated {name} ({v.width}x{v.height}, {v.duration_s}s)" + (f" - '{resolved_title}'" if resolved_title else ""),
                )
            else:
                v = Video.objects.create(
                    file_key=key,
                    file_name=name,
                    public_url=pub_url,
                    file_hash=etag,
                    size_bytes=meta.get("size_bytes") or size,
                    duration_s=meta.get("duration_s"),
                    width=meta.get("width"),
                    height=meta.get("height"),
                    status="invalid",
                    invalid_reason=reason,
                    queue_position=next_pos,
                )
                ActivityLog.objects.create(
                    video_id=v.id,
                    event="invalid",
                    message=f"Validation failed for {name}: {reason}",
                )

    return imported_count


def derive_overall_status(video: Video) -> str:
    """
    Derives overall video status based on YouTube, Instagram, and Facebook platform statuses.
    - All enabled platforms posted or skipped, and at least one posted -> posted, and set delete_after = max(posted_times) + retention_days.
    - No platform pending/uploading, and at least one failed -> failed.
    - Otherwise stays uploading.
    """
    statuses = [video.yt_status, video.ig_status]
    # Include Facebook if it's connected or has been processed (not initial untouched pending)
    if video.fb_status != "pending" or Account.objects.filter(platform="facebook", status="connected").exists():
        statuses.append(video.fb_status)

    now = get_now()
    retention_days = int(get_setting("retention_days", 7))

    if all(s in ("posted", "skipped") for s in statuses) and any(s == "posted" for s in statuses):
        video.status = "posted"
        posted_times = [t for t in [video.yt_posted_at, video.ig_posted_at, video.fb_posted_at] if t]
        base_time = max(posted_times) if posted_times else now
        video.delete_after = base_time + timedelta(days=retention_days)
    elif not any(s in ("pending", "uploading") for s in statuses) and any(s == "failed" for s in statuses):
        video.status = "failed"
    elif video.status in ("queued", "scheduled", "awaiting_approval"):
        # Preserve existing queue / scheduled / approval state; do not change to uploading
        pass
    else:
        video.status = "uploading"

    video.save(update_fields=["status", "delete_after", "updated_at"])
    return video.status


def run_uploader():
    """
    Process dispatchable platform jobs one at a time using an in-process lock.
    Handles YouTube and Instagram independently, retries with backoff, and quota deferral.
    """
    if not _uploader_lock.acquire(blocking=False):
        return

    try:
        now = get_now()
        paused = get_setting("paused", False)
        if paused:
            return

        # 1. Process YouTube Candidate
        if not get_setting("paused_yt", False):
            v_yt = Video.objects.filter(
                status="uploading",
                yt_status="pending",
                approved_at__isnull=False,
            ).filter(
                models.Q(yt_next_attempt_at__isnull=True) | models.Q(yt_next_attempt_at__lte=now)
            ).order_by("queue_position", "created_at").first()

            if v_yt:
                # Atomic claim
                claimed = Video.objects.filter(id=v_yt.id, yt_status="pending").update(yt_status="uploading")
                if claimed == 1:
                    v_yt.refresh_from_db()
                    # Idempotency check
                    if v_yt.yt_video_id:
                        v_yt.yt_status = "posted"
                        v_yt.save(update_fields=["yt_status", "updated_at"])
                        derive_overall_status(v_yt)
                    else:
                        ActivityLog.objects.create(
                            video_id=v_yt.id,
                            event="uploading",
                            platform="youtube",
                            message=f"Starting YouTube upload for {v_yt.file_name} (attempt {v_yt.yt_attempts + 1})",
                        )
                        publish_at_dt = None
                        if getattr(settings, "YOUTUBE_NATIVE_SCHEDULE", True) and v_yt.scheduled_at and v_yt.scheduled_at > get_now():
                            publish_at_dt = v_yt.scheduled_at

                        success, err, data = upload_video_to_youtube(v_yt, publish_at=publish_at_dt)
                        if success and data:
                            v_yt.yt_video_id = data["video_id"]
                            v_yt.yt_url = data["url"]
                            v_yt.yt_posted_at = get_now()
                            v_yt.yt_status = "posted"
                            v_yt.save(update_fields=["yt_video_id", "yt_url", "yt_posted_at", "yt_status", "updated_at"])
                            msg = f"Posted to YouTube Shorts: {v_yt.yt_url}"
                            if publish_at_dt:
                                msg = f"Uploaded to YouTube (Scheduled for release at {publish_at_dt.strftime('%Y-%m-%d %H:%M')} IST): {v_yt.yt_url}"
                            ActivityLog.objects.create(
                                video_id=v_yt.id,
                                event="posted",
                                platform="youtube",
                                message=msg,
                            )
                            derive_overall_status(v_yt)
                            notify_post_success(v_yt, "youtube")
                        else:
                            if err == "quotaExceeded":
                                v_yt.yt_status = "pending"
                                v_yt.yt_next_attempt_at = now + timedelta(hours=8)
                                v_yt.yt_error = "YouTube quotaExceeded: deferred"
                                v_yt.save(update_fields=["yt_status", "yt_next_attempt_at", "yt_error", "updated_at"])
                                ActivityLog.objects.create(
                                    video_id=v_yt.id,
                                    event="retry",
                                    platform="youtube",
                                    message="YouTube quota exceeded. Deferring attempt without counting toward retry limit.",
                                )
                            else:
                                v_yt.yt_attempts += 1
                                if v_yt.yt_attempts >= 3:
                                    v_yt.yt_status = "failed"
                                    v_yt.yt_error = err
                                    v_yt.save(update_fields=["yt_status", "yt_attempts", "yt_error", "updated_at"])
                                    ActivityLog.objects.create(
                                        video_id=v_yt.id,
                                        event="failed",
                                        platform="youtube",
                                        message=f"YouTube failed after 3 attempts: {err}",
                                    )
                                    derive_overall_status(v_yt)
                                    notify_post_failure(v_yt, "youtube", err or "Unknown error")
                                else:
                                    backoffs = {1: timedelta(minutes=5), 2: timedelta(minutes=30)}
                                    v_yt.yt_status = "pending"
                                    v_yt.yt_next_attempt_at = now + backoffs.get(v_yt.yt_attempts, timedelta(hours=2))
                                    v_yt.yt_error = err
                                    v_yt.save(update_fields=["yt_status", "yt_attempts", "yt_next_attempt_at", "yt_error", "updated_at"])
                                    ActivityLog.objects.create(
                                        video_id=v_yt.id,
                                        event="retry",
                                        platform="youtube",
                                        message=f"YouTube attempt {v_yt.yt_attempts} failed ({err}); retry at {v_yt.yt_next_attempt_at.isoformat()}",
                                    )
                    return  # Process one job at a time

        # 2. Process Instagram Candidate
        if not get_setting("paused_ig", False):
            v_ig = Video.objects.filter(
                status="uploading",
                ig_status="pending",
                approved_at__isnull=False,
            ).filter(
                models.Q(ig_next_attempt_at__isnull=True) | models.Q(ig_next_attempt_at__lte=now)
            ).order_by("queue_position", "created_at").first()

            if v_ig:
                claimed = Video.objects.filter(id=v_ig.id, ig_status="pending").update(ig_status="uploading")
                if claimed == 1:
                    v_ig.refresh_from_db()
                    if v_ig.ig_media_id:
                        v_ig.ig_status = "posted"
                        v_ig.save(update_fields=["ig_status", "updated_at"])
                        derive_overall_status(v_ig)
                    else:
                        ActivityLog.objects.create(
                            video_id=v_ig.id,
                            event="uploading",
                            platform="instagram",
                            message=f"Starting Instagram Reels publish for {v_ig.file_name} (attempt {v_ig.ig_attempts + 1})",
                        )
                        success, err, data = publish_reel_to_instagram(v_ig)
                        if success and data:
                            v_ig.ig_media_id = data["media_id"]
                            v_ig.ig_permalink = data["permalink"]
                            v_ig.ig_posted_at = get_now()
                            v_ig.ig_status = "posted"
                            v_ig.save(update_fields=["ig_media_id", "ig_permalink", "ig_posted_at", "ig_status", "updated_at"])
                            ActivityLog.objects.create(
                                video_id=v_ig.id,
                                event="posted",
                                platform="instagram",
                                message=f"Posted to Instagram Reels: {v_ig.ig_permalink}",
                            )
                            derive_overall_status(v_ig)
                            notify_post_success(v_ig, "instagram")
                        else:
                            if "not connected" in (err or "").lower() or "skipped in local mode" in (err or "").lower():
                                v_ig.ig_status = "skipped"
                                v_ig.ig_error = err
                                v_ig.save(update_fields=["ig_status", "ig_error", "updated_at"])
                                ActivityLog.objects.create(
                                    video_id=v_ig.id,
                                    event="skipped",
                                    platform="instagram",
                                    message=err,
                                )
                                derive_overall_status(v_ig)
                            elif err == "rateLimit":
                                v_ig.ig_status = "pending"
                                v_ig.ig_next_attempt_at = now + timedelta(hours=1)
                                v_ig.ig_error = "Instagram rate limit: deferred 1 hour"
                                v_ig.save(update_fields=["ig_status", "ig_next_attempt_at", "ig_error", "updated_at"])
                                ActivityLog.objects.create(
                                    video_id=v_ig.id,
                                    event="retry",
                                    platform="instagram",
                                    message="Instagram rate limit hit. Deferring 1 hour without counting attempt.",
                                )
                            else:
                                v_ig.ig_attempts += 1
                                if v_ig.ig_attempts >= 3:
                                    v_ig.ig_status = "failed"
                                    v_ig.ig_error = err
                                    v_ig.save(update_fields=["ig_status", "ig_attempts", "ig_error", "updated_at"])
                                    ActivityLog.objects.create(
                                        video_id=v_ig.id,
                                        event="failed",
                                        platform="instagram",
                                        message=f"Instagram failed after 3 attempts: {err}",
                                    )
                                    derive_overall_status(v_ig)
                                    notify_post_failure(v_ig, "instagram", err or "Unknown error")
                                else:
                                    backoffs = {1: timedelta(minutes=5), 2: timedelta(minutes=30)}
                                    v_ig.ig_status = "pending"
                                    v_ig.ig_next_attempt_at = now + backoffs.get(v_ig.ig_attempts, timedelta(hours=2))
                                    v_ig.ig_error = err
                                    v_ig.save(update_fields=["ig_status", "ig_attempts", "ig_next_attempt_at", "ig_error", "updated_at"])
                                    ActivityLog.objects.create(
                                        video_id=v_ig.id,
                                        event="retry",
                                        platform="instagram",
                                        message=f"Instagram attempt {v_ig.ig_attempts} failed ({err}); retry at {v_ig.ig_next_attempt_at.isoformat()}",
                                    )

        # 3. Process Facebook Candidate
        if not get_setting("paused_fb", False):
            v_fb = Video.objects.filter(
                status="uploading",
                fb_status="pending",
                approved_at__isnull=False,
            ).filter(
                models.Q(fb_next_attempt_at__isnull=True) | models.Q(fb_next_attempt_at__lte=now)
            ).order_by("queue_position", "created_at").first()

            if v_fb:
                claimed = Video.objects.filter(id=v_fb.id, fb_status="pending").update(fb_status="uploading")
                if claimed == 1:
                    v_fb.refresh_from_db()
                    if v_fb.fb_video_id:
                        v_fb.fb_status = "posted"
                        v_fb.save(update_fields=["fb_status", "updated_at"])
                        derive_overall_status(v_fb)
                    else:
                        ActivityLog.objects.create(
                            video_id=v_fb.id,
                            event="uploading",
                            platform="facebook",
                            message=f"Starting Facebook Page publish for {v_fb.file_name} (attempt {v_fb.fb_attempts + 1})",
                        )
                        success, err, data = publish_video_to_facebook(v_fb)
                        if success and data:
                            v_fb.fb_video_id = data["video_id"]
                            v_fb.fb_url = data["permalink"]
                            v_fb.fb_posted_at = get_now()
                            v_fb.fb_status = "posted"
                            v_fb.save(update_fields=["fb_video_id", "fb_url", "fb_posted_at", "fb_status", "updated_at"])
                            ActivityLog.objects.create(
                                video_id=v_fb.id,
                                event="posted",
                                platform="facebook",
                                message=f"Posted to Facebook Page: {v_fb.fb_url}",
                            )
                            derive_overall_status(v_fb)
                            notify_post_success(v_fb, "facebook")
                        else:
                            if "not connected" in (err or "").lower():
                                v_fb.fb_status = "skipped"
                                v_fb.fb_error = err
                                v_fb.save(update_fields=["fb_status", "fb_error", "updated_at"])
                                ActivityLog.objects.create(
                                    video_id=v_fb.id,
                                    event="skipped",
                                    platform="facebook",
                                    message=err,
                                )
                                derive_overall_status(v_fb)
                            elif err == "rateLimit":
                                v_fb.fb_status = "pending"
                                v_fb.fb_next_attempt_at = now + timedelta(hours=1)
                                v_fb.fb_error = "Facebook rate limit: deferred 1 hour"
                                v_fb.save(update_fields=["fb_status", "fb_next_attempt_at", "fb_error", "updated_at"])
                                ActivityLog.objects.create(
                                    video_id=v_fb.id,
                                    event="retry",
                                    platform="facebook",
                                    message="Facebook rate limit hit. Deferring 1 hour without counting attempt.",
                                )
                            else:
                                v_fb.fb_attempts += 1
                                if v_fb.fb_attempts >= 3:
                                    v_fb.fb_status = "failed"
                                    v_fb.fb_error = err
                                    v_fb.save(update_fields=["fb_status", "fb_attempts", "fb_error", "updated_at"])
                                    ActivityLog.objects.create(
                                        video_id=v_fb.id,
                                        event="failed",
                                        platform="facebook",
                                        message=f"Facebook failed after 3 attempts: {err}",
                                    )
                                    derive_overall_status(v_fb)
                                    notify_post_failure(v_fb, "facebook", err or "Unknown error")
                                else:
                                    backoffs = {1: timedelta(minutes=5), 2: timedelta(minutes=30)}
                                    v_fb.fb_status = "pending"
                                    v_fb.fb_next_attempt_at = now + backoffs.get(v_fb.fb_attempts, timedelta(hours=2))
                                    v_fb.fb_error = err
                                    v_fb.save(update_fields=["fb_status", "fb_attempts", "fb_next_attempt_at", "fb_error", "updated_at"])
                                    ActivityLog.objects.create(
                                        video_id=v_fb.id,
                                        event="retry",
                                        platform="facebook",
                                        message=f"Facebook attempt {v_fb.fb_attempts} failed ({err}); retry at {v_fb.fb_next_attempt_at.isoformat()}",
                                    )
    finally:
        _uploader_lock.release()


def run_cleanup():
    """
    Daily cleanup job:
    1. Delete storage files for videos where status='posted', keep=False,
       file_deleted_at is null, delete_after <= now.
    2. Prune activity_log rows older than 90 days.
    """
    now = get_now()
    eligible_videos = Video.objects.filter(
        status="posted",
        keep=False,
        file_deleted_at__isnull=True,
        delete_after__lte=now,
    )

    for v in eligible_videos:
        try:
            delete_file(v.file_key)
            v.file_deleted_at = now
            v.status = "file_deleted"
            v.save(update_fields=["file_deleted_at", "status", "updated_at"])
            ActivityLog.objects.create(
                video_id=v.id,
                event="deleted",
                message=f"Cleaned up storage file {v.file_name} after retention period.",
            )
        except Exception as e:
            logger.error("Failed to cleanup file for video %s: %s", v.id, e)

    # Prune activity_log older than 90 days
    cutoff = now - timedelta(days=90)
    pruned, _ = ActivityLog.objects.filter(created_at__lt=cutoff).delete()
    if pruned > 0:
        logger.info("Pruned %d activity_log entries older than 90 days.", pruned)


def run_storage_check():
    """
    Hourly storage and database limit check:
    1. Compares DB size_bytes sum with storage bucket files; warns if diff > 5%.
    2. Checks PostgreSQL database size; warns if > 400 MB.
    """
    from django.db import connection
    now = get_now()

    db_used_bytes = Video.objects.filter(file_deleted_at__isnull=True).aggregate(
        models.Sum("size_bytes")
    )["size_bytes__sum"] or 0

    try:
        inbox_items = list_inbox_videos()
        bucket_bytes = sum(item.get("size", 0) for item in inbox_items)
        if db_used_bytes > 0 and bucket_bytes > 0:
            diff_ratio = abs(bucket_bytes - db_used_bytes) / max(bucket_bytes, db_used_bytes)
            if diff_ratio > 0.05:
                ActivityLog.objects.create(
                    event="warning",
                    message=f"Storage discrepancy > 5%: Bucket has {bucket_bytes} bytes, DB reports {db_used_bytes} bytes.",
                )
    except Exception as e:
        logger.warning("Storage bucket check failed: %s", e)

    if connection.vendor == "postgresql":
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_database_size(current_database());")
                row = cursor.fetchone()
                if row:
                    pg_bytes = row[0]
                    if pg_bytes > 400 * 1024 * 1024:
                        ActivityLog.objects.create(
                            event="warning",
                            message=f"PostgreSQL database size is {round(pg_bytes / (1024*1024), 1)} MB (> 400 MB / 80% free limit).",
                        )
        except Exception as e:
            logger.warning("Postgres size check failed: %s", e)


def run_token_refresh():
    """Daily token refresh for Instagram and YouTube."""
    try:
        refresh_instagram_token()
    except Exception as e:
        logger.error("Instagram token refresh error: %s", e)

    try:
        get_valid_youtube_access_token()
    except Exception as e:
        logger.error("YouTube token refresh error: %s", e)


def start_scheduler():

    global scheduler
    if scheduler and scheduler.running:
        return scheduler

    scheduler = BackgroundScheduler(daemon=True)

    scheduler.add_job(
        run_job_safely,
        "interval",
        minutes=5,
        args=["watcher", run_watcher],
        id="watcher",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        run_job_safely,
        "interval",
        minutes=1,
        args=["tick", run_tick],
        id="tick",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        run_job_safely,
        "interval",
        seconds=30,
        args=["uploader", run_uploader],
        id="uploader",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        run_job_safely,
        CronTrigger(hour=3, minute=0),
        args=["cleanup", run_cleanup],
        id="cleanup",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        run_job_safely,
        "interval",
        hours=24,
        args=["token_refresh", run_token_refresh],
        id="token_refresh",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        run_job_safely,
        "interval",
        hours=1,
        args=["storage_check", run_storage_check],
        id="storage_check",
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    logger.info("APScheduler started successfully.")

    # Reset stuck uploads from previous restarts
    try:
        reset_stuck_uploads()
    except Exception as e:
        logger.error("Error resetting stuck uploads on startup: %s", e)

    # Run tick once immediately on startup to catch up
    try:
        run_job_safely("tick_startup", run_tick)
    except Exception as e:
        logger.error("Startup tick failed: %s", e)

    return scheduler
