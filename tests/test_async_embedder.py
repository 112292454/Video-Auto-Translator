"""异步嵌字队列契约测试。"""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

from vat.embedder.async_embedder import AsyncEmbedderQueue, EmbedTask, EmbedTaskStatus
from vat.models import TaskStatus, TaskStep


class TestAsyncEmbedderQueue:
    def test_process_task_passes_gpu_device_without_positional_shift(self, tmp_path):
        queue = AsyncEmbedderQueue(gpu_devices=[0], max_concurrent_per_gpu=1)
        queue.ffmpeg.embed_subtitle_hard = MagicMock(return_value=True)

        task = EmbedTask(
            video_id=1,
            video_path=tmp_path / "video.mp4",
            subtitle_path=tmp_path / "subtitle.ass",
            output_path=tmp_path / "output.mp4",
            video_codec="hevc",
            audio_codec="copy",
            crf=26,
            preset="p4",
            use_gpu=True,
            gpu_id=2,
        )

        success = asyncio.run(queue._process_task(task))

        assert success is True
        queue.ffmpeg.embed_subtitle_hard.assert_called_once_with(
            video_path=task.video_path,
            subtitle_path=task.subtitle_path,
            output_path=task.output_path,
            video_codec="hevc",
            audio_codec="copy",
            crf=26,
            preset="p4",
            gpu_device="cuda:2",
        )

    def test_process_task_uses_auto_gpu_device_when_gpu_disabled(self, tmp_path):
        queue = AsyncEmbedderQueue(gpu_devices=[0], max_concurrent_per_gpu=1)
        queue.ffmpeg.embed_subtitle_hard = MagicMock(return_value=True)

        task = EmbedTask(
            video_id=2,
            video_path=Path(tmp_path / "video.mp4"),
            subtitle_path=Path(tmp_path / "subtitle.ass"),
            output_path=Path(tmp_path / "output.mp4"),
            use_gpu=False,
            gpu_id=0,
        )

        success = asyncio.run(queue._process_task(task))

        assert success is True
        assert queue.ffmpeg.embed_subtitle_hard.call_args.kwargs["gpu_device"] == "auto"

    def test_module_imports_without_legacy_database_aliases(self):
        import importlib

        module = importlib.import_module("vat.embedder.async_embedder")
        assert hasattr(module, "AsyncEmbedderQueue")

    def test_update_database_status_writes_embed_task_status(self, tmp_path, monkeypatch):
        queue = AsyncEmbedderQueue(
            gpu_devices=[0],
            max_concurrent_per_gpu=1,
            database_path=tmp_path / "vat.db",
            output_base_dir=tmp_path,
        )

        fake_db = MagicMock()
        monkeypatch.setattr("vat.embedder.async_embedder.Database", lambda *args, **kwargs: fake_db)

        task = EmbedTask(
            video_id="vid-1",
            video_path=tmp_path / "video.mp4",
            subtitle_path=tmp_path / "subtitle.ass",
            output_path=tmp_path / "final.mp4",
        )
        task.status = EmbedTaskStatus.COMPLETED

        asyncio.run(queue._update_database_status(task))

        fake_db.update_task_status.assert_called_once_with(
            "vid-1",
            TaskStep.EMBED,
            TaskStatus.COMPLETED,
        )
