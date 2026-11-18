"""
Email notification service using SMTP.
Sends notifications for final results (success or failure after exhausting retries).
Failures in email sending are logged and never crash the caller.
"""
import logging
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from django.conf import settings
from core.models import ActivityLog, Video
from core.settings_store import get_config_value, get_setting

logger = logging.getLogger(__name__)


def _send_email_worker(subject: str, body: str, recipient: str, video_id=None):
    from django.db import close_old_connections
    close_old_connections()
    smtp_host = str(get_config_value("smtp_host", getattr(settings, "SMTP_HOST", ""))).strip()
    smtp_port = int(get_config_value("smtp_port", getattr(settings, "SMTP_PORT", 587)))
    smtp_user = str(get_config_value("smtp_user", getattr(settings, "SMTP_USER", ""))).strip()
    smtp_password = str(get_config_value("smtp_password", getattr(settings, "SMTP_PASSWORD", ""))).strip()
    smtp_from = str(get_config_value("smtp_from", getattr(settings, "SMTP_FROM", "")) or smtp_user).strip()
    starttls = get_config_value("smtp_starttls", getattr(settings, "SMTP_STARTTLS", True))
    if isinstance(starttls, str):
        starttls = starttls.lower() in ("true", "1", "yes")

    if not smtp_host or not smtp_user or not recipient:
        logger.warning("SMTP not fully configured; skipping email.")
        return

    msg = MIMEMultipart()
    msg["From"] = smtp_from
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            if starttls:
                server.starttls()
            if smtp_password:
                server.login(smtp_user, smtp_password)
            server.send_message(msg)

        try:
            ActivityLog.objects.create(
                video_id=video_id,
                event="email_sent",
                message=f"Notification email sent to {recipient}: '{subject}'",
            )
        except Exception:
            pass
    except Exception as e:
        logger.error("Failed to send notification email: %s", e)
        try:
            ActivityLog.objects.create(
                video_id=video_id,
                event="email_failed",
                message=f"Failed to send email to {recipient}: {e}",
            )
        except Exception:
            pass
    finally:
        close_old_connections()


def _send_email(subject: str, body: str, recipient: str, video_id=None, async_send: bool = True):
    """Send SMTP email in background thread or synchronously."""
    if async_send:
        t = threading.Thread(target=_send_email_worker, args=(subject, body, recipient, video_id), daemon=True)
        t.start()
        return t
    else:
        _send_email_worker(subject, body, recipient, video_id)
        return None


def notify_post_success(video: Video, platform: str, async_send: bool = True) -> None:
    """Send success email for posted video."""
    enabled = get_setting("notify_enabled", True)
    recipient = get_setting("notify_email", "")
    if not enabled or not recipient:
        return

    if platform == "youtube":
        plat_name = "YouTube"
        link = video.yt_url
    elif platform == "facebook":
        plat_name = "Facebook Page"
        link = video.fb_url
    else:
        plat_name = "Instagram"
        link = video.ig_permalink

    title = video.yt_title or video.file_name
    subject = f"[Shorts] Posted to {plat_name}: {title}"
    app_url = getattr(settings, "APP_PUBLIC_URL", "http://localhost:8000")
    video_page = f"{app_url}/videos/{video.id}/"

    body = f"""Your Short has been successfully posted!

Platform: {plat_name}
Title: {title}
File: {video.file_name}
Sequence: #{video.seq}

Live Post URL: {link}
Video Details: {video_page}
"""
    _send_email(subject, body, recipient, video_id=video.id, async_send=async_send)


def notify_post_failure(video: Video, platform: str, error_message: str, async_send: bool = True) -> None:
    """Send failure email when retries are exhausted."""
    enabled = get_setting("notify_enabled", True)
    recipient = get_setting("notify_email", "")
    if not enabled or not recipient:
        return

    if platform == "youtube":
        plat_name = "YouTube"
        attempts = video.yt_attempts
    elif platform == "facebook":
        plat_name = "Facebook Page"
        attempts = video.fb_attempts
    else:
        plat_name = "Instagram"
        attempts = video.ig_attempts

    title = video.yt_title or video.file_name
    subject = f"[Shorts] FAILED on {plat_name}: {title}"

    app_url = getattr(settings, "APP_PUBLIC_URL", "http://localhost:8000")
    video_page = f"{app_url}/videos/{video.id}/"

    body = f"""An automated post failed after exhausting all {attempts} retries.

Platform: {plat_name}
Title: {title}
File: {video.file_name}
Error Details: {error_message}

Review and retry this video in your dashboard:
{video_page}
"""
    _send_email(subject, body, recipient, video_id=video.id, async_send=async_send)


def notify_test_email(recipient: str, async_send: bool = True) -> None:
    """Send a manual test email from the settings dashboard."""
    subject = "[Shorts] Test Notification"
    body = "This is a test notification from your Shorts Auto-Poster server. If you received this, SMTP email alerts are working correctly!"
    _send_email(subject, body, recipient, async_send=async_send)
