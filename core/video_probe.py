"""
Video probe validation using ffprobe.
Validates MP4 container, h264 video codec, aac audio codec, vertical aspect ratio (height > width),
duration <= 90s, and size <= MAX_FILE_MB.
"""
import json
import logging
import subprocess
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from django.conf import settings

logger = logging.getLogger(__name__)


def probe_video(url: str, file_size_bytes: int = 0, file_key: Optional[str] = None) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """
    Probe a video URL or local file path via ffprobe.
    Returns:
        (is_valid: bool, invalid_reason: Optional[str], metadata: dict)
        metadata contains: duration_s, width, height, size_bytes
    """
    from core.storage import get_local_file_path

    max_mb = getattr(settings, "MAX_FILE_MB", 50)
    max_bytes = max_mb * 1024 * 1024

    metadata: Dict[str, Any] = {
        "duration_s": None,
        "width": None,
        "height": None,
        "size_bytes": file_size_bytes,
    }

    if file_size_bytes > max_bytes:
        mb_actual = round(file_size_bytes / (1024 * 1024), 1)
        reason = f"File size ({mb_actual} MB) exceeds {max_mb} MB limit."
        return False, reason, metadata

    target_path = url
    if file_key:
        local_p = get_local_file_path(file_key)
        if local_p and local_p.exists():
            target_path = str(local_p)

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        target_path,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "ffprobe probe timed out reading video stream.", metadata
    except FileNotFoundError:
        logger.error("ffprobe executable not found on system PATH.")
        return False, "ffprobe not found on server.", metadata
    except Exception as e:
        logger.error("Unexpected error invoking ffprobe: %s", e)
        return False, f"Failed to probe video: {e}", metadata

    if proc.returncode != 0:
        err = proc.stderr.strip() or "Unknown error reading media"
        return False, f"ffprobe error: {err}", metadata

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, "Invalid ffprobe JSON output.", metadata

    fmt = data.get("format", {})
    streams = data.get("streams", [])

    # Check container format
    format_name = fmt.get("format_name", "").lower()
    if not any(f in format_name for f in ["mp4", "mov", "m4v"]):
        return False, f"Container must be MP4 (detected format: {format_name}).", metadata

    # Parse streams
    video_stream = None
    audio_stream = None

    for s in streams:
        c_type = s.get("codec_type")
        if c_type == "video" and not video_stream:
            video_stream = s
        elif c_type == "audio" and not audio_stream:
            audio_stream = s

    if not video_stream:
        return False, "Missing video stream.", metadata

    # Video codec check: h264
    v_codec = video_stream.get("codec_name", "").lower()
    if v_codec != "h264":
        return False, f"Video codec must be h264 (found: {v_codec}).", metadata

    # Audio check: must be aac
    if not audio_stream:
        return False, "Missing audio stream (Shorts/Reels require audio).", metadata

    a_codec = audio_stream.get("codec_name", "").lower()
    if a_codec != "aac":
        return False, f"Audio codec must be aac (found: {a_codec}).", metadata

    # Dimensions check: vertical (height > width)
    width = video_stream.get("width")
    height = video_stream.get("height")
    if not width or not height:
        return False, "Unable to determine video resolution.", metadata

    metadata["width"] = int(width)
    metadata["height"] = int(height)

    if metadata["height"] <= metadata["width"]:
        return False, f"Video is not vertical: height ({height}) must be greater than width ({width}).", metadata

    # Duration check: <= 90s
    duration_raw = fmt.get("duration") or video_stream.get("duration")
    if duration_raw:
        try:
            dur_decimal = Decimal(str(duration_raw)).quantize(Decimal("0.01"))
            metadata["duration_s"] = dur_decimal
            if dur_decimal > Decimal("90.0"):
                return False, f"Duration ({dur_decimal}s) exceeds 90 seconds limit.", metadata
        except Exception:
            pass

    # Update size if not given
    if not metadata["size_bytes"] and fmt.get("size"):
        try:
            metadata["size_bytes"] = int(fmt.get("size"))
            if metadata["size_bytes"] > max_bytes:
                mb_actual = round(metadata["size_bytes"] / (1024 * 1024), 1)
                return False, f"File size ({mb_actual} MB) exceeds {max_mb} MB limit.", metadata
        except Exception:
            pass

    return True, None, metadata
