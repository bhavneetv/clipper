"""
Caption resolution for Shorts Auto-Poster.
Resolves captions separately for YouTube and Instagram at post time.
Supports {filename} and {number} placeholders, character limits, tags, and fallback hierarchy.
"""
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from core.models import Video
from core.settings_store import get_setting


def apply_placeholders(text: Optional[str], video: Video) -> str:
    """Replace {filename} and {number} in text."""
    if not text:
        return ""
    base_name = os.path.splitext(video.file_name)[0]
    seq_str = str(video.seq or "")
    res = text.replace("{filename}", base_name).replace("{number}", seq_str)
    return res


def truncate_at_word_boundary(text: str, max_len: int) -> str:
    """Truncate text at word boundary to fit within max_len."""
    text = text.strip()
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space].strip()
    return truncated.strip()


def resolve_youtube_caption(video: Video) -> Tuple[Dict[str, Any], str]:
    """
    Resolve YouTube caption (title, description, tags).
    Priority:
    1. Video's own yt_title / yt_description / hashtags
    2. caption_yt from settings
    3. default_caption from settings
    Returns (resolved_dict, source_str) where source_str is 'video' | 'platform' | 'default'.
    """
    default_cap = get_setting("default_caption") or {"title": "", "description": "", "hashtags": []}
    platform_cap = get_setting("caption_yt")

    source = "default"
    title_raw = ""
    desc_raw = ""
    tags_raw: List[str] = []

    # 1. Video own caption check
    if video.yt_title or video.yt_description or (video.hashtags is not None):
        source = "video"
        title_raw = video.yt_title or ""
        desc_raw = video.yt_description or ""
        tags_raw = video.hashtags if video.hashtags is not None else []
    # 2. Platform specific caption
    elif platform_cap and any(platform_cap.get(k) for k in ("title", "description", "hashtags")):
        source = "platform"
        title_raw = platform_cap.get("title", "")
        desc_raw = platform_cap.get("description", "")
        tags_raw = platform_cap.get("hashtags", [])
    # 3. Default fallback
    else:
        source = "default"
        title_raw = default_cap.get("title", "")
        desc_raw = default_cap.get("description", "")
        tags_raw = default_cap.get("hashtags", [])

    # If title is empty anywhere in the hierarchy, fallback to filename without ext
    base_name = os.path.splitext(video.file_name)[0]
    if not title_raw:
        title_raw = default_cap.get("title") or base_name

    title = apply_placeholders(title_raw, video).strip()
    if not title:
        title = base_name

    title = truncate_at_word_boundary(title, 100)

    # Description
    desc = apply_placeholders(desc_raw, video).strip()
    if "#Shorts" not in desc and "#shorts" not in desc:
        desc = f"{desc}\n\n#Shorts".strip()

    # Tags: clean '#' and strip
    tags: List[str] = []
    if isinstance(tags_raw, list):
        for t in tags_raw:
            cleaned = str(t).strip().lstrip("#")
            if cleaned and cleaned not in tags:
                tags.append(cleaned)

    return {
        "title": title,
        "description": desc,
        "tags": tags,
    }, source


def resolve_instagram_caption(video: Video) -> Tuple[str, str]:
    """
    Resolve Instagram caption text with hashtags.
    Priority:
    1. Video's own ig_caption (+ hashtags)
    2. caption_ig from settings
    3. default_caption from settings
    Returns (resolved_caption, source_str).
    """
    default_cap = get_setting("default_caption") or {"title": "", "description": "", "hashtags": []}
    platform_cap = get_setting("caption_ig")

    source = "default"
    caption_raw = ""
    tags_raw: List[str] = []

    # 1. Video own caption check
    if video.ig_caption or (video.hashtags is not None):
        source = "video"
        caption_raw = video.ig_caption or ""
        tags_raw = video.hashtags if video.hashtags is not None else []
    # 2. Platform specific caption
    elif platform_cap and any(platform_cap.get(k) for k in ("title", "description", "hashtags")):
        source = "platform"
        caption_raw = platform_cap.get("description") or platform_cap.get("title", "")
        tags_raw = platform_cap.get("hashtags", [])
    # 3. Default fallback
    else:
        source = "default"
        caption_raw = default_cap.get("description") or default_cap.get("title", "")
        tags_raw = default_cap.get("hashtags", [])

    caption = apply_placeholders(caption_raw, video).strip()

    # Append hashtags as #tag
    tags_list: List[str] = []
    if isinstance(tags_raw, list):
        for t in tags_raw:
            cleaned = str(t).strip()
            if cleaned:
                tag = f"#{cleaned.lstrip('#')}"
                if tag not in tags_list:
                    tags_list.append(tag)

    # Instagram allows up to 30 hashtags
    tags_list = tags_list[:30]
    if tags_list:
        tags_block = " ".join(tags_list)
        if caption:
            caption = f"{caption}\n\n{tags_block}"
        else:
            caption = tags_block

    # Instagram limit: 2,200 chars
    if len(caption) > 2200:
        caption = truncate_at_word_boundary(caption, 2200)

    return caption, source


def resolve_facebook_caption(video: Video) -> Tuple[Dict[str, Any], str]:
    """
    Resolve Facebook video / reel caption (title, description with hashtags).
    Priority:
    1. Video's own fb_caption or yt_title / yt_description
    2. caption_fb from settings
    3. default_caption from settings
    Returns (resolved_dict, source_str) with keys 'title' and 'description'.
    """
    default_cap = get_setting("default_caption") or {"title": "", "description": "", "hashtags": []}
    platform_cap = get_setting("caption_fb")

    source = "default"
    title_raw = ""
    desc_raw = ""
    tags_raw: List[str] = []

    if video.fb_caption or video.yt_title or (video.hashtags is not None):
        source = "video"
        title_raw = video.yt_title or ""
        desc_raw = video.fb_caption or video.yt_description or ""
        tags_raw = video.hashtags if video.hashtags is not None else []
    elif platform_cap and any(platform_cap.get(k) for k in ("title", "description", "hashtags")):
        source = "platform"
        title_raw = platform_cap.get("title", "")
        desc_raw = platform_cap.get("description", "")
        tags_raw = platform_cap.get("hashtags", [])
    else:
        source = "default"
        title_raw = default_cap.get("title", "")
        desc_raw = default_cap.get("description", "")
        tags_raw = default_cap.get("hashtags", [])

    base_name = os.path.splitext(video.file_name)[0]
    if not title_raw:
        title_raw = default_cap.get("title") or base_name

    title = apply_placeholders(title_raw, video).strip()
    if not title:
        title = base_name
    title = truncate_at_word_boundary(title, 255)

    desc = apply_placeholders(desc_raw, video).strip()

    tags_list: List[str] = []
    if isinstance(tags_raw, list):
        for t in tags_raw:
            cleaned = str(t).strip()
            if cleaned:
                tag = f"#{cleaned.lstrip('#')}"
                if tag not in tags_list:
                    tags_list.append(tag)

    if tags_list:
        tags_block = " ".join(tags_list)
        if desc:
            desc = f"{desc}\n\n{tags_block}"
        else:
            desc = tags_block

    if len(desc) > 5000:
        desc = truncate_at_word_boundary(desc, 5000)

    return {
        "title": title,
        "description": desc or title,
    }, source
