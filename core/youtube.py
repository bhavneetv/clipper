"""
YouTube Shorts integration.
Implements OAuth 2.0, token management, and resumable streaming uploads without buffering full videos in memory.
"""
from datetime import timedelta
import json
import logging
from typing import Any, Dict, Optional, Tuple
import urllib.parse
from django.conf import settings
from django.utils import timezone
import requests
from core.captions import resolve_youtube_caption
from core.models import Account, ActivityLog, Video
from core.security import decrypt_token, encrypt_token
from core.settings_store import get_config_value, get_setting

logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


def get_youtube_oauth_url(state: str) -> str:
    """Generate OAuth consent URL for YouTube upload scope."""
    client_id = str(get_config_value("google_client_id", getattr(settings, "GOOGLE_CLIENT_ID", ""))).strip()
    redirect_uri = str(get_config_value("google_redirect_uri", getattr(settings, "GOOGLE_REDIRECT_URI", ""))).strip()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": YOUTUBE_UPLOAD_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_youtube_code(code: str) -> Tuple[bool, Optional[str]]:
    """Exchange authorization code for access and refresh tokens, saving to Account."""
    client_id = str(get_config_value("google_client_id", getattr(settings, "GOOGLE_CLIENT_ID", ""))).strip()
    client_secret = str(get_config_value("google_client_secret", getattr(settings, "GOOGLE_CLIENT_SECRET", ""))).strip()
    redirect_uri = str(get_config_value("google_redirect_uri", getattr(settings, "GOOGLE_REDIRECT_URI", ""))).strip()

    data = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        res = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=15)
        if res.status_code != 200:
            return False, f"Google token exchange failed: {res.text}"
        token_data = res.json()
    except Exception as e:
        return False, f"Request to Google token endpoint failed: {e}"

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)
    expires_at = timezone.now() + timedelta(seconds=expires_in)

    acc, _ = Account.objects.get_or_create(platform="youtube")
    acc.access_token_enc = encrypt_token(access_token)
    if refresh_token:
        acc.refresh_token_enc = encrypt_token(refresh_token)
    acc.expires_at = expires_at
    acc.status = "connected"

    # Try fetching channel title
    try:
        ch_res = requests.get(
            "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if ch_res.status_code == 200:
            items = ch_res.json().get("items", [])
            if items:
                acc.account_name = items[0]["snippet"].get("title")
    except Exception:
        pass

    if not acc.account_name:
        try:
            tinfo = requests.get(f"https://www.googleapis.com/oauth2/v3/tokeninfo?access_token={access_token}", timeout=10)
            if tinfo.status_code == 200 and "youtube" in tinfo.json().get("scope", ""):
                acc.account_name = "YouTube Channel"
        except Exception:
            pass

    acc.save()
    ActivityLog.objects.create(
        event="token_refreshed",
        platform="youtube",
        message=f"YouTube connected successfully for channel: {acc.account_name or 'Unknown'}",
    )
    return True, None


def get_valid_youtube_access_token() -> Optional[str]:
    """Retrieve or refresh valid access token for YouTube."""
    acc = Account.objects.filter(platform="youtube").first()
    if not acc or not acc.refresh_token_enc:
        return None

    # Check if existing access token is still valid (>5 minutes remaining)
    if acc.access_token_enc and acc.expires_at and acc.expires_at > timezone.now() + timedelta(minutes=5):
        return decrypt_token(acc.access_token_enc)

    # Refresh token
    refresh_token = decrypt_token(acc.refresh_token_enc)
    client_id = str(get_config_value("google_client_id", getattr(settings, "GOOGLE_CLIENT_ID", ""))).strip()
    client_secret = str(get_config_value("google_client_secret", getattr(settings, "GOOGLE_CLIENT_SECRET", ""))).strip()

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        res = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=15)
        if res.status_code != 200:
            acc.status = "error"
            acc.save(update_fields=["status", "updated_at"])
            ActivityLog.objects.create(
                event="error",
                platform="youtube",
                message=f"YouTube token refresh failed: {res.text}",
            )
            return None
        data = res.json()
        new_access = data.get("access_token")
        expires_in = data.get("expires_in", 3600)
        acc.access_token_enc = encrypt_token(new_access)
        acc.expires_at = timezone.now() + timedelta(seconds=expires_in)
        acc.status = "connected"
        acc.save(update_fields=["access_token_enc", "expires_at", "status", "updated_at"])
        return new_access
    except Exception as e:
        logger.error("Failed to refresh YouTube token: %s", e)
        acc.status = "error"
        acc.save(update_fields=["status", "updated_at"])
        return None


def upload_video_to_youtube(video: Video, publish_at: Optional[Any] = None) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Uploads a video to YouTube using chunked resumable upload.
    Supports streaming directly from local disk (Local Mode) or streamed HTTP public URL.
    Supports YouTube native scheduling (publishAt) when publish_at is provided.
    """
    from core.storage import get_local_file_path

    token = get_valid_youtube_access_token()
    if not token:
        return False, "YouTube account not connected or token expired.", None

    caption, _ = resolve_youtube_caption(video)
    privacy = get_setting("youtube_privacy", "public")

    status_dict: Dict[str, Any] = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": False,
    }

    # YouTube Native Scheduling: If publish_at is specified, privacy MUST be private
    if publish_at is not None:
        status_dict["privacyStatus"] = "private"
        if hasattr(publish_at, "astimezone"):
            utc_dt = publish_at.astimezone(timezone.UTC)
            status_dict["publishAt"] = utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    metadata = {
        "snippet": {
            "title": caption["title"],
            "description": caption["description"],
            "tags": caption.get("tags", []),
            "categoryId": "22",
        },
        "status": status_dict,
    }

    local_p = get_local_file_path(video.file_key)
    file_size = local_p.stat().st_size if (local_p and local_p.exists()) else video.size_bytes

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
    }
    if file_size > 0:
        headers["X-Upload-Content-Length"] = str(file_size)

    # 1. Initiate Resumable Upload Session
    init_url = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
    try:
        init_res = requests.post(init_url, headers=headers, json=metadata, timeout=20)
        if init_res.status_code == 403:
            err_json = init_res.json() if init_res.text else {}
            reason = err_json.get("error", {}).get("errors", [{}])[0].get("reason", "")
            if "quotaExceeded" in str(init_res.text) or reason == "quotaExceeded":
                return False, "quotaExceeded", None
            return False, f"YouTube API Error (403): {init_res.text}", None
        elif init_res.status_code not in (200, 201):
            return False, f"Failed to initialize YouTube resumable upload: {init_res.status_code} {init_res.text}", None

        upload_url = init_res.headers.get("Location")
        if not upload_url:
            return False, "Missing Location header from YouTube upload initialization.", None
    except Exception as e:
        return False, f"YouTube upload initialization request error: {e}", None

    # 2. Upload video in chunks (5 MiB)
    CHUNK_SIZE = 5 * 1024 * 1024
    try:
        response_json = None
        uploaded_bytes = 0

        if local_p and local_p.exists():
            # 2a. Stream directly from local PC hard drive
            total_bytes = local_p.stat().st_size
            with open(local_p, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    start = uploaded_bytes
                    end = uploaded_bytes + len(chunk) - 1
                    total_str = str(total_bytes) if total_bytes > 0 else "*"
                    chunk_headers = {
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{end}/{total_str}",
                    }
                    c_res = requests.put(upload_url, headers=chunk_headers, data=chunk, timeout=60)
                    if c_res.status_code in (200, 201):
                        response_json = c_res.json()
                        uploaded_bytes += len(chunk)
                        break
                    elif c_res.status_code == 308:
                        uploaded_bytes += len(chunk)
                    else:
                        return False, f"Chunk upload failed: HTTP {c_res.status_code} {c_res.text}", None

        else:
            # 2b. Stream video from public URL in chunks
            with requests.get(video.public_url, stream=True, timeout=30) as video_stream:
                if video_stream.status_code != 200:
                    return False, f"Failed to stream video from public URL: HTTP {video_stream.status_code}", None

                total_bytes = video.size_bytes
                buffer = bytearray()
                for chunk in video_stream.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        buffer.extend(chunk)
                        while len(buffer) >= CHUNK_SIZE:
                            chunk_to_send = bytes(buffer[:CHUNK_SIZE])
                            del buffer[:CHUNK_SIZE]
                            start = uploaded_bytes
                            end = uploaded_bytes + len(chunk_to_send) - 1
                            total_str = str(total_bytes) if total_bytes > 0 else "*"
                            chunk_headers = {
                                "Content-Length": str(len(chunk_to_send)),
                                "Content-Range": f"bytes {start}-{end}/{total_str}",
                            }
                            c_res = requests.put(upload_url, headers=chunk_headers, data=chunk_to_send, timeout=60)
                            if c_res.status_code in (200, 201):
                                response_json = c_res.json()
                                uploaded_bytes += len(chunk_to_send)
                                break
                            elif c_res.status_code == 308:
                                uploaded_bytes += len(chunk_to_send)
                            else:
                                return False, f"Chunk upload failed: HTTP {c_res.status_code} {c_res.text}", None

                # Upload final remaining buffer
                if len(buffer) > 0 and not response_json:
                    final_chunk = bytes(buffer)
                    start = uploaded_bytes
                    end = uploaded_bytes + len(final_chunk) - 1
                    final_total = str(uploaded_bytes + len(final_chunk))
                    chunk_headers = {
                        "Content-Length": str(len(final_chunk)),
                        "Content-Range": f"bytes {start}-{end}/{final_total}",
                    }
                    c_res = requests.put(upload_url, headers=chunk_headers, data=final_chunk, timeout=60)
                    if c_res.status_code in (200, 201):
                        response_json = c_res.json()
                    else:
                        return False, f"Final chunk upload failed: HTTP {c_res.status_code} {c_res.text}", None

        if not response_json:
            return False, "Upload completed but did not receive YouTube video response.", None

        yt_id = response_json.get("id")
        return True, None, {
            "video_id": yt_id,
            "url": f"https://youtube.com/shorts/{yt_id}",
        }
    except Exception as e:
        return False, f"Streamed upload failed: {e}", None
