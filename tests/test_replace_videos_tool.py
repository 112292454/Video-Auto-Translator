from vat.cli.tools import _collect_replace_video_candidates
from vat.database import Database
from vat.models import SourceType, TaskStatus, TaskStep, Video


def _add_video(db, video_id, *, aid=None, embed_status=TaskStatus.COMPLETED):
    metadata = {}
    if aid is not None:
        metadata["bilibili_aid"] = aid
    db.add_video(
        Video(
            id=video_id,
            source_type=SourceType.YOUTUBE,
            source_url=f"https://youtube.com/watch?v={video_id}",
            title=f"Video {video_id}",
            metadata=metadata,
        )
    )
    db.update_task_status(video_id, TaskStep.EMBED, embed_status)


def test_collect_replace_video_candidates_requires_completed_embed_aid_and_final(tmp_path):
    db = Database(str(tmp_path / "database.db"), output_base_dir=str(tmp_path / "videos"))
    _add_video(db, "ok-video", aid=1001)
    _add_video(db, "failed-video", aid=1002, embed_status=TaskStatus.FAILED)
    _add_video(db, "no-aid")
    _add_video(db, "no-final", aid=1004)

    ok_dir = tmp_path / "videos" / "ok-video"
    ok_dir.mkdir(parents=True)
    (ok_dir / "final.mp4").write_bytes(b"video")

    candidates, skipped = _collect_replace_video_candidates(
        db,
        video_ids=["ok-video", "failed-video", "no-aid", "no-final", "missing"],
    )

    assert [item["video_id"] for item in candidates] == ["ok-video"]
    assert candidates[0]["aid"] == 1001
    reasons = {item["video_id"]: item["reason"] for item in skipped}
    assert reasons["failed-video"] == "embed_not_completed:failed"
    assert reasons["no-aid"] == "missing_bilibili_aid"
    assert reasons["no-final"] == "missing_or_empty_final_mp4"
    assert reasons["missing"] == "video_not_found"


def test_collect_replace_video_candidates_can_skip_verified_current_final(tmp_path):
    db = Database(str(tmp_path / "database.db"), output_base_dir=str(tmp_path / "videos"))
    _add_video(db, "verified-video", aid=1001)

    video_dir = tmp_path / "videos" / "verified-video"
    video_dir.mkdir(parents=True)
    final_video = video_dir / "final.mp4"
    final_video.write_bytes(b"video")
    stat = final_video.stat()

    video = db.get_video("verified-video")
    metadata = dict(video.metadata or {})
    metadata["bilibili_ops"] = {
        "replace_video": {
            "state": "verified",
            "new_video_path": str(final_video),
            "new_video_size": stat.st_size,
            "new_video_mtime_ns": stat.st_mtime_ns,
        }
    }
    db.update_video("verified-video", metadata=metadata)

    candidates, skipped = _collect_replace_video_candidates(
        db,
        video_ids=["verified-video"],
        skip_verified=True,
    )

    assert candidates == []
    assert skipped == [
        {"video_id": "verified-video", "reason": "already_verified_current_final_mp4"}
    ]

    final_video.write_bytes(b"new video")
    candidates, skipped = _collect_replace_video_candidates(
        db,
        video_ids=["verified-video"],
        skip_verified=True,
    )

    assert [item["video_id"] for item in candidates] == ["verified-video"]
    assert skipped == []
