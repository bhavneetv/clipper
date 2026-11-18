"""
Facebook Pages video and Reels integration using Meta Graph API.
Supports direct local file upload as well as cloud URL streaming (Cloudinary/Supabase).
"""
import logging
import os
from typing import Any, Dict, Optional, Tuple
from django.conf import settings
from django.utils import timezone
import requests
from core.captions import resolve_facebook_caption
from core.models import Account, ActivityLog, Video
from core.security import decrypt_token, encrypt_token
from core.settings_store import get_config_value
from core.storage import get_local_file_path

logger = logging.getLogger(__name__)


def get_graph_api_base() -> str:
    version = get_config_value("graph_api_version", getattr(settings, "GRAPH_API_VERSION", "v21.0"))
    return f"https://graph.facebook.com/{version}"


def save_facebook_credentials(token_input: str, page_id_input: str = "") -> Tuple[bool, Optional[str]]:
    """
    Save Facebook Page ID and Access Token, verifying access with Graph API.
    Supports both Page Access Tokens and User Access Tokens with Page admin permissions.
    If page_id_input is empty, automatically inspects /me/accounts to discover page ID and page token.
    Also automatically links connected Instagram Business account if found!
    """
    token = token_input.strip()
    page_id = page_id_input.strip() if page_id_input else ""

    if not token:
        return False, "Access Token is required."

    base = get_graph_api_base()
    try:
        discovered_page_id = None
        discovered_token = None
        discovered_ig_id = None
        page_name = None

        # 1. Try to inspect /me/accounts (works for User Tokens)
        me_res = requests.get(
            f"{base}/me/accounts",
            params={"fields": "name,id,access_token,instagram_business_account", "access_token": token},
            timeout=15,
        )
        if me_res.status_code == 200:
            accounts_data = me_res.json().get("data", [])
            if accounts_data:
                target_acc = None
                if page_id:
                    for a in accounts_data:
                        if str(a.get("id")) == str(page_id):
                            target_acc = a
                            break
                if not target_acc:
                    target_acc = accounts_data[0]

                discovered_page_id = target_acc.get("id")
                page_name = target_acc.get("name")
                discovered_token = target_acc.get("access_token")
                ig_data = target_acc.get("instagram_business_account")
                if ig_data and ig_data.get("id"):
                    discovered_ig_id = ig_data.get("id")

        # 2. If not discovered via /me/accounts (e.g. token is already a Page Access Token)
        if not discovered_token:
            if not page_id:
                # Query /me to discover ID
                me_info = requests.get(f"{base}/me", params={"fields": "id,name", "access_token": token}, timeout=15)
                if me_info.status_code == 200:
                    mdata = me_info.json()
                    page_id = mdata.get("id")
                    page_name = mdata.get("name")

            if not page_id:
                return False, "Could not auto-detect Page ID. Please provide Facebook Page ID."

            res = requests.get(
                f"{base}/{page_id}",
                params={"fields": "name,access_token,instagram_business_account", "access_token": token},
                timeout=15,
            )
            if res.status_code != 200:
                err_msg = "Unknown error"
                try:
                    err_msg = res.json().get("error", {}).get("message", res.text)
                except Exception:
                    err_msg = res.text
                return False, f"Meta Graph API error ({res.status_code}): {err_msg}"

            data = res.json()
            page_name = data.get("name") or page_name or f"Page {page_id}"
            discovered_token = data.get("access_token") or token
            discovered_page_id = page_id
            ig_data = data.get("instagram_business_account")
            if ig_data and ig_data.get("id"):
                discovered_ig_id = ig_data.get("id")

        final_token = discovered_token or token
        final_page_id = discovered_page_id or page_id

        acc, _ = Account.objects.get_or_create(platform="facebook")
        acc.account_name = page_name or f"Page {final_page_id}"
        acc.fb_page_id = str(final_page_id)
        acc.access_token_enc = encrypt_token(final_token)
        acc.status = "connected"
        acc.expires_at = None  # Page tokens typically do not expire
        acc.save()

        ActivityLog.objects.create(
            event="token_refreshed",
            platform="facebook",
            message=f"Facebook Page '{acc.account_name}' (ID: {final_page_id}) connected successfully.",
        )

        # Auto-link connected Instagram Business account if present
        if discovered_ig_id:
            from core.instagram import save_instagram_credentials
            save_instagram_credentials(final_token, str(discovered_ig_id))

        # Reset any failed videos so they immediately retry on next tick
        reset_count = Video.objects.filter(status="failed").update(
            status="queued",
            fb_attempts=0,
            ig_attempts=0,
            fb_next_attempt_at=None,
            ig_next_attempt_at=None,
        )
        if reset_count:
            logger.info("Reset %d failed video(s) to queued after Facebook token refresh.", reset_count)

        return True, None
    except Exception as e:
        logger.exception("Failed to verify Facebook Page credentials")
        return False, f"Failed to connect Facebook Page: {e}"


def get_valid_facebook_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Return (access_token, page_id) for Facebook."""
    acc = Account.objects.filter(platform="facebook").first()
    if not acc or not acc.access_token_enc or not acc.fb_page_id:
        return None, None
    try:
        token = decrypt_token(acc.access_token_enc)
        return token, acc.fb_page_id
    except Exception:
        return None, None


def publish_video_to_facebook(video: Video) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Publish video / reel to Facebook Page:
    - If local file exists, uploads binary directly via multipart form (works in Local Storage Mode!)
    - If remote URL exists (Cloudinary/Supabase), streams via file_url parameter
    """
    token, page_id = get_valid_facebook_credentials()
    if not token or not page_id:
        return False, "Facebook Page not connected or token missing.", None

    caption_dict, _ = resolve_facebook_caption(video)
    title = caption_dict.get("title", video.file_name)
    description = caption_dict.get("description", "")
    base = get_graph_api_base()
    url = f"{base}/{page_id}/videos"

    local_path = get_local_file_path(video.file_key)
    try:
        if local_path and os.path.exists(str(local_path)):
            # Direct binary multipart upload (Local PC mode supported)
            with open(str(local_path), "rb") as f:
                files = {"source": (video.file_name, f, "video/mp4")}
                data = {
                    "access_token": token,
                    "title": title,
                    "description": description,
                }
                res = requests.post(url, data=data, files=files, timeout=240)
        elif video.public_url and video.public_url.startswith("http"):
            # Remote cloud streaming (Cloudinary / Supabase)
            data = {
                "access_token": token,
                "title": title,
                "description": description,
                "file_url": video.public_url,
            }
            res = requests.post(url, data=data, timeout=60)
        else:
            return False, "Video file not found locally or on public cloud storage.", None

        if res.status_code == 200:
            video_id = res.json().get("id")
            if not video_id:
                return False, f"Facebook returned success but missing video ID: {res.text}", None

            # Attempt to resolve canonical permalink
            permalink = f"https://www.facebook.com/{page_id}/videos/{video_id}/"
            try:
                p_res = requests.get(
                    f"{base}/{video_id}",
                    params={"fields": "permalink_url", "access_token": token},
                    timeout=10,
                )
                if p_res.status_code == 200 and p_res.json().get("permalink_url"):
                    permalink = p_res.json()["permalink_url"]
            except Exception:
                pass

            return True, None, {"video_id": str(video_id), "permalink": permalink}

        # Handle errors
        err_data = {}
        try:
            err_data = res.json().get("error", {})
        except Exception:
            pass
        code = err_data.get("code")
        err_msg = err_data.get("message", res.text)

        if code in (4, 17, 32, 613):
            return False, "rateLimit", None

        return False, f"Facebook API error ({res.status_code}): {err_msg}", None

    except requests.exceptions.Timeout:
        return False, "Facebook upload request timed out.", None
    except Exception as e:
        logger.exception("Error publishing video to Facebook")
        return False, str(e), None
