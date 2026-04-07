"""Bilibili 共享支撑逻辑。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

from ..uploaders.bilibili import BilibiliUploader

_YOUTUBE_URL_RE = re.compile(r"youtube\.com/watch\?v=([a-zA-Z0-9_-]+)")
_TITLE_SUFFIX_RE = re.compile(r"\s*\|\s*#\d+\s*$")


def build_bilibili_uploader(
    config,
    *,
    with_upload_params: bool = False,
    project_root: Path,
):
    """基于统一配置构造 BilibiliUploader。"""
    bilibili_config = config.uploader.bilibili
    kwargs = {
        "cookies_file": str(Path(project_root) / bilibili_config.cookies_file),
        "lock_db_path": config.storage.database_path,
        "upload_interval": bilibili_config.upload_interval,
        "max_concurrent_uploads": getattr(
            getattr(config, "concurrency", None),
            "max_concurrent_uploads",
            1,
        ),
    }
    if with_upload_params:
        kwargs["line"] = bilibili_config.line
        kwargs["threads"] = bilibili_config.threads
    return BilibiliUploader(**kwargs)


def resolve_video_file(video, config) -> Optional[Path]:
    """从视频记录解析本地视频文件路径。"""
    candidates = []
    if getattr(video, "output_dir", None):
        candidates.append(Path(video.output_dir) / "final.mp4")
    vid_dir = Path(config.storage.output_dir) / video.id
    candidates.append(vid_dir / "final.mp4")
    candidates.append(vid_dir / f"{video.id}.mp4")

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def find_local_video_for_aid(aid: int, config, db, uploader) -> Optional[Path]:
    """根据 B站 aid 查找对应的本地视频文件。"""
    yt_video_id, bili_title = _read_bilibili_archive_info(aid, uploader)

    if yt_video_id:
        video = db.get_video(yt_video_id)
        if video:
            path = resolve_video_file(video, config)
            if path:
                return path

    videos = db.list_videos()
    for video in videos:
        metadata = getattr(video, "metadata", None) or {}
        if str(metadata.get("bilibili_aid", "")) == str(aid):
            path = resolve_video_file(video, config)
            if path:
                return path

    if bili_title:
        clean_title = _TITLE_SUFFIX_RE.sub("", bili_title).strip()
        for video in videos:
            metadata = getattr(video, "metadata", None) or {}
            translated = metadata.get("translated", {}) or {}
            translated_title = translated.get("title_translated", "")
            if (
                translated_title
                and clean_title
                and (clean_title in translated_title or translated_title in clean_title)
            ):
                path = resolve_video_file(video, config)
                if path:
                    return path

    if yt_video_id:
        vid_dir = Path(config.storage.output_dir) / yt_video_id
        for name in ["final.mp4", f"{yt_video_id}.mp4"]:
            candidate = vid_dir / name
            if candidate.exists():
                return candidate

    return None


def _read_bilibili_archive_info(aid: int, uploader) -> Tuple[Optional[str], str]:
    yt_video_id = None
    bili_title = ""

    try:
        detail = uploader.get_archive_detail(aid)
        if detail:
            archive = detail.get("archive", {})
            bili_title = archive.get("title", "")
            source = archive.get("source", "")
            desc = archive.get("desc", "")
            full_desc = uploader._get_full_desc(aid)
            for text in [source, full_desc or desc, desc]:
                match = _YOUTUBE_URL_RE.search(text)
                if match:
                    yt_video_id = match.group(1)
                    break
    except Exception:
        pass

    return yt_video_id, bili_title
