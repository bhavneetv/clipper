"""
Multi-backend Storage integration for Shorts Auto-Poster.
Supports:
- "local": Reads from local PC directory (default ./inbox), zero-latency scanning, local file streaming.
- "cloudinary": 25 GB free storage with direct public CDN URLs for YouTube & Instagram.
- "supabase": Supabase Storage bucket with signed URLs and public CDN.
"""
import logging
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
from django.conf import settings
from supabase import Client, create_client
from core.settings_store import get_config_value

logger = logging.getLogger(__name__)

_supabase_client: Optional[Client] = None


def get_storage_backend() -> str:
    """Return configured backend: 'local', 'cloudinary', or 'supabase'."""
    val = get_config_value("storage_backend")
    if val:
        return str(val).lower().strip()
    return getattr(settings, "STORAGE_BACKEND", "local").lower().strip()


def get_local_inbox_dir() -> Path:
    """Return Path to local inbox directory, ensuring it exists."""
    p = get_config_value("local_inbox_dir", getattr(settings, "LOCAL_INBOX_DIR", None))
    if not p:
        p = settings.BASE_DIR / "inbox"
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_local_file_path(key: str) -> Optional[Path]:
    """If key is a local file, return the absolute Path on disk if it exists."""
    if not key:
        return None
    if key.startswith("local:"):
        filename = key[len("local:"):]
        p = get_local_inbox_dir() / filename
        if p.exists():
            return p
    return None


def get_supabase_client() -> Optional[Client]:
    """Return a cached Supabase client if configured."""
    global _supabase_client

    url = str(get_config_value("supabase_url", getattr(settings, "SUPABASE_URL", ""))).strip()
    key = str(get_config_value("supabase_service_role_key", getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", ""))).strip()
    if not url or not key:
        return None

    if _supabase_client is not None and getattr(_supabase_client, "_init_url", "") == url and getattr(_supabase_client, "_init_key", "") == key:
        return _supabase_client

    try:
        client = create_client(url, key)
        client._init_url = url
        client._init_key = key
        _supabase_client = client
        return _supabase_client
    except Exception as e:
        logger.error("Failed to initialize Supabase client: %s", e)
        return None


def get_bucket_client():
    """Return storage bucket client for Supabase."""
    client = get_supabase_client()
    if not client:
        return None
    bucket_name = str(get_config_value("supabase_bucket", getattr(settings, "SUPABASE_BUCKET", "shorts"))).strip()
    return client.storage.from_(bucket_name)


def get_public_url(key: str) -> str:
    """Return public or local accessible URL for a video key."""
    if key.startswith("local:"):
        filename = key[len("local:"):]
        app_url = str(get_config_value("app_public_url", getattr(settings, "APP_PUBLIC_URL", "http://localhost:8080"))).rstrip("/")
        return f"{app_url}/media/local/{filename}"

    if key.startswith("cloudinary:"):
        pub_id = key[len("cloudinary:"):]
        cloud_name = str(get_config_value("cloudinary_cloud_name", getattr(settings, "CLOUDINARY_CLOUD_NAME", ""))).strip()
        api_key = str(get_config_value("cloudinary_api_key", getattr(settings, "CLOUDINARY_API_KEY", ""))).strip()
        api_secret = str(get_config_value("cloudinary_api_secret", getattr(settings, "CLOUDINARY_API_SECRET", ""))).strip()
        try:
            import cloudinary.utils
            cloudinary.config(
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=api_secret,
                secure=True,
            )
            url, _ = cloudinary.utils.cloudinary_url(pub_id, resource_type="video", secure=True)
            return url
        except Exception as e:
            logger.error("Cloudinary URL error: %s", e)
            return f"https://res.cloudinary.com/{cloud_name}/video/upload/{pub_id}"

    # Default / Supabase
    bucket = get_bucket_client()
    if not bucket:
        url = str(get_config_value("supabase_url", getattr(settings, "SUPABASE_URL", ""))).rstrip("/")
        bucket_name = str(get_config_value("supabase_bucket", getattr(settings, "SUPABASE_BUCKET", "shorts"))).strip()
        return f"{url}/storage/v1/object/public/{bucket_name}/{key}"
    return bucket.get_public_url(key)


def list_inbox_videos(prefix: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List all .mp4 files under inbox based on the active STORAGE_BACKEND.
    """
    backend = get_storage_backend()

    # 1. Local Storage Backend
    if backend == "local":
        inbox_dir = get_local_inbox_dir()
        results: List[Dict[str, Any]] = []
        try:
            for item in inbox_dir.iterdir():
                if item.is_file() and item.name.lower().endswith(".mp4"):
                    stat = item.stat()
                    results.append({
                        "name": item.name,
                        "key": f"local:{item.name}",
                        "size": stat.st_size,
                        "etag": f"{stat.st_mtime}_{stat.st_size}",
                        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_ctime)),
                    })
        except Exception as e:
            logger.error("Error listing local inbox items: %s", e)
        return results

    # 2. Cloudinary Backend
    elif backend == "cloudinary":
        results = []
        try:
            import cloudinary
            import cloudinary.api
            cloud_name = str(get_config_value("cloudinary_cloud_name", getattr(settings, "CLOUDINARY_CLOUD_NAME", ""))).strip()
            api_key = str(get_config_value("cloudinary_api_key", getattr(settings, "CLOUDINARY_API_KEY", ""))).strip()
            api_secret = str(get_config_value("cloudinary_api_secret", getattr(settings, "CLOUDINARY_API_SECRET", ""))).strip()
            cloudinary.config(
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=api_secret,
                secure=True,
            )
            folder = (prefix or "shorts/inbox").strip("/")
            res = cloudinary.api.resources(
                resource_type="video",
                type="upload",
                prefix=folder,
                max_results=100,
            )
            for r in res.get("resources", []):
                public_id = r.get("public_id", "")
                filename = public_id.split("/")[-1] + "." + r.get("format", "mp4")
                results.append({
                    "name": filename,
                    "key": f"cloudinary:{public_id}",
                    "size": r.get("bytes", 0),
                    "etag": str(r.get("version", "")),
                    "created_at": r.get("created_at"),
                })
        except Exception as e:
            logger.error("Error listing Cloudinary inbox items: %s", e)
        return results

    # 3. Supabase Backend (Default fallback)
    else:
        if prefix is None:
            prefix = str(get_config_value("storage_inbox_prefix", getattr(settings, "STORAGE_INBOX_PREFIX", "inbox/")))

        clean_prefix = prefix.strip("/")
        bucket = get_bucket_client()
        if not bucket:
            logger.warning("No Supabase bucket client available to list videos.")
            return []

        results = []
        limit = 100
        offset = 0

        while True:
            try:
                items = bucket.list(clean_prefix, {"limit": limit, "offset": offset})
                if not items:
                    break

                for item in items:
                    name = item.get("name", "")
                    if name.lower().endswith(".mp4"):
                        metadata = item.get("metadata") or {}
                        size = metadata.get("size") or 0
                        etag = metadata.get("eTag") or metadata.get("etag") or item.get("id") or ""
                        clean_etag = str(etag).strip('"')
                        key = f"{clean_prefix}/{name}" if clean_prefix else name
                        results.append({
                            "name": name,
                            "key": key,
                            "size": size,
                            "etag": clean_etag,
                            "created_at": item.get("created_at"),
                        })

                if len(items) < limit:
                    break
                offset += limit
            except Exception as e:
                logger.error("Error listing inbox items from Supabase at offset %d: %s", offset, e)
                break

        return results


def delete_file(key: str) -> bool:
    """Delete a single file from the configured storage backend."""
    if key.startswith("local:"):
        filename = key[len("local:"):]
        filepath = get_local_inbox_dir() / filename
        try:
            if filepath.exists():
                filepath.unlink()
            return True
        except Exception as e:
            logger.error("Failed to delete local file %s: %s", filepath, e)
            return False

    if key.startswith("cloudinary:"):
        pub_id = key[len("cloudinary:"):]
        try:
            import cloudinary
            import cloudinary.uploader
            cloud_name = str(get_config_value("cloudinary_cloud_name", getattr(settings, "CLOUDINARY_CLOUD_NAME", ""))).strip()
            api_key = str(get_config_value("cloudinary_api_key", getattr(settings, "CLOUDINARY_API_KEY", ""))).strip()
            api_secret = str(get_config_value("cloudinary_api_secret", getattr(settings, "CLOUDINARY_API_SECRET", ""))).strip()
            cloudinary.config(
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=api_secret,
                secure=True,
            )
            cloudinary.uploader.destroy(pub_id, resource_type="video")
            return True
        except Exception as e:
            logger.error("Failed to delete Cloudinary video %s: %s", pub_id, e)
            return False

    # Supabase
    bucket = get_bucket_client()
    if not bucket:
        logger.error("No bucket client to delete %s", key)
        return False
    try:
        bucket.remove([key])
        return True
    except Exception as e:
        logger.error("Failed to delete file %s from Supabase: %s", key, e)
        return False


def create_signed_upload_url(file_name: str, prefix: Optional[str] = None) -> Dict[str, Any]:
    """
    Create a signed upload URL or parameters for browser direct uploads.
    """
    backend = get_storage_backend()

    if backend == "local":
        return {
            "signed_url": "/uploads/local",
            "path": f"local:{file_name}",
            "token": "local",
            "method": "POST",
            "backend": "local",
        }

    if backend == "cloudinary":
        import cloudinary
        import cloudinary.utils
        cloud_name = str(get_config_value("cloudinary_cloud_name", getattr(settings, "CLOUDINARY_CLOUD_NAME", ""))).strip()
        api_key = str(get_config_value("cloudinary_api_key", getattr(settings, "CLOUDINARY_API_KEY", ""))).strip()
        api_secret = str(get_config_value("cloudinary_api_secret", getattr(settings, "CLOUDINARY_API_SECRET", ""))).strip()
        if not cloud_name or not api_key or not api_secret:
            raise ValueError("Cloudinary credentials are not configured in settings/env.")

        timestamp = int(time.time())
        folder = (prefix or "shorts/inbox").strip("/")
        params_to_sign = {
            "folder": folder,
            "timestamp": timestamp,
        }
        signature = cloudinary.utils.api_sign_request(params_to_sign, api_secret)
        return {
            "signed_url": f"https://api.cloudinary.com/v1_1/{cloud_name}/video/upload",
            "path": f"cloudinary:{folder}/{file_name}",
            "token": signature,
            "timestamp": timestamp,
            "api_key": api_key,
            "folder": folder,
            "method": "POST",
            "backend": "cloudinary",
        }

    # Supabase
    if prefix is None:
        prefix = str(get_config_value("storage_inbox_prefix", getattr(settings, "STORAGE_INBOX_PREFIX", "inbox/")))
    clean_prefix = prefix.strip("/")
    key = f"{clean_prefix}/{file_name}" if clean_prefix else file_name

    bucket = get_bucket_client()
    if not bucket:
        raise ValueError("Supabase storage bucket is not configured.")

    res = bucket.create_signed_upload_url(key)
    signed_url = res.get("signed_url") or res.get("signedUrl")
    return {
        "signed_url": signed_url,
        "path": key,
        "token": res.get("token"),
        "method": "PUT",
        "backend": "supabase",
    }


def get_storage_usage_stats() -> Dict[str, Any]:
    """
    Calculate storage usage statistics across Local, Cloudinary, and Supabase.
    """
    sb_url = str(get_config_value("supabase_url", getattr(settings, "SUPABASE_URL", ""))).strip()
    sb_bkt = str(get_config_value("supabase_bucket", getattr(settings, "SUPABASE_BUCKET", "shorts"))).strip()
    c_name = str(get_config_value("cloudinary_cloud_name", getattr(settings, "CLOUDINARY_CLOUD_NAME", ""))).strip()

    stats: Dict[str, Any] = {
        "active_backend": get_storage_backend(),
        "local": {
            "used_bytes": 0,
            "used_mb": 0.0,
            "file_count": 0,
            "path": str(get_local_inbox_dir()),
        },
        "supabase": {
            "used_bytes": 0,
            "used_mb": 0.0,
            "limit_mb": 1000,
            "bucket": sb_bkt,
            "configured": bool(sb_url),
        },
        "cloudinary": {
            "used_bytes": 0,
            "used_mb": 0.0,
            "limit_mb": 25000,
            "cloud_name": c_name,
            "configured": bool(c_name),
        },
        "database": {
            "total_videos": 0,
            "total_bytes": 0,
            "total_mb": 0.0,
        }
    }

    # 1. Local storage usage
    try:
        inbox = get_local_inbox_dir()
        if inbox.exists():
            for f in inbox.iterdir():
                if f.is_file() and f.name.lower().endswith(".mp4"):
                    stats["local"]["file_count"] += 1
                    stats["local"]["used_bytes"] += f.stat().st_size
            stats["local"]["used_mb"] = round(stats["local"]["used_bytes"] / (1024 * 1024), 2)
    except Exception as e:
        logger.warning("Error calculating local storage: %s", e)

    # 2. Database video usage
    try:
        from core.models import Video
        from django.db.models import Sum
        db_qs = Video.objects.filter(file_deleted_at__isnull=True)
        stats["database"]["total_videos"] = db_qs.count()
        total_b = db_qs.aggregate(Sum("size_bytes"))["size_bytes__sum"] or 0
        stats["database"]["total_bytes"] = total_b
        stats["database"]["total_mb"] = round(total_b / (1024 * 1024), 2)
    except Exception as e:
        logger.warning("Error calculating DB stats: %s", e)

    # 3. Supabase usage
    if stats["supabase"]["configured"]:
        try:
            from core.models import Video
            from django.db.models import Sum
            sb_bytes = Video.objects.filter(
                file_deleted_at__isnull=True
            ).exclude(file_key__startswith="local:").exclude(file_key__startswith="cloudinary:").aggregate(Sum("size_bytes"))["size_bytes__sum"] or 0
            stats["supabase"]["used_bytes"] = sb_bytes
            stats["supabase"]["used_mb"] = round(sb_bytes / (1024 * 1024), 2)
        except Exception:
            pass

    # 4. Cloudinary usage
    if stats["cloudinary"]["configured"]:
        try:
            from core.models import Video
            from django.db.models import Sum
            c_bytes = Video.objects.filter(
                file_deleted_at__isnull=True,
                file_key__startswith="cloudinary:"
            ).aggregate(Sum("size_bytes"))["size_bytes__sum"] or 0
            stats["cloudinary"]["used_bytes"] = c_bytes
            stats["cloudinary"]["used_mb"] = round(c_bytes / (1024 * 1024), 2)
        except Exception:
            pass

    return stats
