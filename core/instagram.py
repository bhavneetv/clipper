"""
Instagram Reels integration using Meta Graph API.
Implements media container creation, status polling, publication, and long-lived token refresh.
"""
from datetime import timedelta
import logging
import time
from typing import Any, Dict, Optional, Tuple
from django.conf import settings
from django.utils import timezone
import requests
from core.captions import resolve_instagram_caption
from core.models import Account, ActivityLog, Video
from core.security import decrypt_token, encrypt_token
from core.settings_store import get_config_value

logger = logging.getLogger(__name__)


def get_graph_api_base() -> str:
    version = get_config_value("graph_api_version", getattr(settings, "GRAPH_API_VERSION", "v21.0"))
    return f"https://graph.facebook.com/{version}"


def save_instagram_credentials(access_token: str, ig_user_id: str) -> Tuple[bool, Optional[str]]:
    """Save manual Instagram access token and verify user account."""
    base = get_graph_api_base()
    try:
        res = requests.get(
            f"{base}/{ig_user_id}",
            params={"fields": "username,name", "access_token": access_token},
            timeout=10,
        )
        if res.status_code != 200:
            return False, f"Invalid Instagram credentials: {res.text}"
        data = res.json()
        account_name = data.get("username") or data.get("name") or f"User {ig_user_id}"
    except Exception as e:
        return False, f"Failed to verify Instagram account: {e}"

    acc, _ = Account.objects.get_or_create(platform="instagram")
    acc.access_token_enc = encrypt_token(access_token)
    acc.ig_user_id = ig_user_id
    acc.account_name = account_name
    acc.status = "connected"
    # Set default 60-day expiry for long-lived Meta token
    acc.expires_at = timezone.now() + timedelta(days=60)
    acc.save()

    ActivityLog.objects.create(
        event="token_refreshed",
        platform="instagram",
        message=f"Instagram connected successfully for @{account_name}",
    )
    return True, None


def get_valid_instagram_access_token() -> Tuple[Optional[str], Optional[str]]:
    """Return (access_token, ig_user_id) for Instagram."""
    acc = Account.objects.filter(platform="instagram").first()
    if not acc or not acc.access_token_enc or not acc.ig_user_id:
        return None, None
    try:
        token = decrypt_token(acc.access_token_enc)
        return token, acc.ig_user_id
    except Exception:
        return None, None


def refresh_instagram_token() -> bool:
    """
    Refreshes long-lived Instagram User token if fewer than 10 days remain.
    """
    acc = Account.objects.filter(platform="instagram").first()
    if not acc or not acc.access_token_enc:
        return False

    # Check if fewer than 10 days remain
    if acc.expires_at and (acc.expires_at - timezone.now()).days > 10:
        return True

    token = decrypt_token(acc.access_token_enc)
    base = get_graph_api_base()
    url = f"{base}/refresh_access_token"
    params = {
        "grant_type": "ig_refresh_token",
        "access_token": token,
    }

    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code != 200:
            acc.status = "error"
            acc.save(update_fields=["status", "updated_at"])
            ActivityLog.objects.create(
                event="error",
                platform="instagram",
                message=f"Instagram token refresh failed: {res.text}",
            )
            return False

        data = res.json()
        new_token = data.get("access_token")
        expires_in = data.get("expires_in", 60 * 24 * 3600)  # default 60 days
        acc.access_token_enc = encrypt_token(new_token)
        acc.expires_at = timezone.now() + timedelta(seconds=expires_in)
        acc.status = "connected"
        acc.save(update_fields=["access_token_enc", "expires_at", "status", "updated_at"])

        ActivityLog.objects.create(
            event="token_refreshed",
            platform="instagram",
            message="Instagram long-lived token refreshed successfully.",
        )
        return True
    except Exception as e:
        logger.error("Failed to refresh Instagram token: %s", e)
        acc.status = "error"
        acc.save(update_fields=["status", "updated_at"])
        return False


def publish_reel_to_instagram(video: Video) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Publish video to Instagram Reels:
    1. Create media container
    2. Poll status until FINISHED
    3. Publish media container
    4. Fetch permalink
    """
    if video.file_key.startswith("local:") or not video.public_url.startswith("http"):
        return False, "Instagram Reels requires public cloud storage (e.g. Cloudinary or Supabase). Skipped in Local Mode.", None

    token, ig_user_id = get_valid_instagram_access_token()
    if not token or not ig_user_id:
        return False, "Instagram account not connected or token missing.", None

    caption, _ = resolve_instagram_caption(video)
    base = get_graph_api_base()

    # 1. Create Media Container
    container_url = f"{base}/{ig_user_id}/media"
    container_data = {
        "media_type": "REELS",
        "video_url": video.public_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": token,
    }

    try:
        res = requests.post(container_url, data=container_data, timeout=25)
        res_json = res.json() if res.text else {}

        if res.status_code != 200:
            err = res_json.get("error", {})
            err_code = err.get("code")
            if err_code in (4, 17, 32, 613) or "rate limit" in str(res.text).lower() or "limit reached" in str(res.text).lower():
                return False, "rateLimit", None
            return False, f"Instagram container creation error: {res.text}", None

        creation_id = res_json.get("id")
        if not creation_id:
            return False, f"No creation_id returned: {res.text}", None
    except Exception as e:
        return False, f"Request to create Instagram container failed: {e}", None

    # 2. Poll Status until FINISHED (up to 5 minutes, checking every 10 seconds)
    status_url = f"{base}/{creation_id}"
    start_time = time.time()
    max_wait = 300  # 5 minutes

    while time.time() - start_time < max_wait:
        time.sleep(10)
        try:
            poll_res = requests.get(
                status_url,
                params={"fields": "status_code", "access_token": token},
                timeout=15,
            )
            if poll_res.status_code != 200:
                return False, f"Instagram status check failed: {poll_res.text}", None

            status_code = poll_res.json().get("status_code")
            if status_code == "FINISHED":
                break
            elif status_code == "ERROR":
                return False, "Instagram video processing failed with status ERROR.", None
            elif status_code == "EXPIRED":
                return False, "Instagram video container EXPIRED before publishing.", None
        except Exception as e:
            logger.warning("Instagram poll exception: %s", e)

    else:
        return False, "Instagram video processing timed out after 5 minutes.", None

    # 3. Publish Media Container
    publish_url = f"{base}/{ig_user_id}/media_publish"
    try:
        pub_res = requests.post(
            publish_url,
            data={"creation_id": creation_id, "access_token": token},
            timeout=25,
        )
        if pub_res.status_code != 200:
            return False, f"Instagram media publish failed: {pub_res.text}", None
        pub_json = pub_res.json()
        ig_media_id = pub_json.get("id")
        if not ig_media_id:
            return False, f"No media ID in publish response: {pub_res.text}", None
    except Exception as e:
        return False, f"Request to publish Instagram media failed: {e}", None

    # 4. Fetch Permalink
    permalink = ""
    try:
        link_res = requests.get(
            f"{base}/{ig_media_id}",
            params={"fields": "permalink", "access_token": token},
            timeout=10,
        )
        if link_res.status_code == 200:
            permalink = link_res.json().get("permalink", "")
    except Exception:
        pass

    return True, None, {
        "media_id": ig_media_id,
        "permalink": permalink or f"https://www.instagram.com/reel/{ig_media_id}/",
    }
