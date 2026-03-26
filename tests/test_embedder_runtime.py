"""embedder runtime 契约测试。"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from vat.embedder.ffmpeg_wrapper import FFmpegWrapper


class TestFFmpegWrapperInit:
    def test_init_raises_when_ffmpeg_missing(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)

        with pytest.raises(RuntimeError, match="ffmpeg未安装"):
            FFmpegWrapper()


class TestFFmpegWrapperHelpers:
    def test_check_encoder_support_returns_false_on_subprocess_error(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

        assert wrapper._check_encoder_support("h264_nvenc") is False

    def test_get_video_info_parses_ffprobe_json(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        video.write_bytes(b"00")

        payload = {
            "format": {"duration": "10.5", "size": "12345", "bit_rate": "999"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "r_frame_rate": "30000/1001"},
                {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
            ],
        }
        monkeypatch.setattr(
            "subprocess.run",
            lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(payload)),
        )

        info = wrapper.get_video_info(video)

        assert info["duration"] == 10.5
        assert info["video"]["codec"] == "h264"
        assert info["audio"]["codec"] == "aac"

    def test_get_video_info_returns_none_on_subprocess_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        video.write_bytes(b"00")
        monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

        assert wrapper.get_video_info(video) is None

    def test_scale_ass_style_applies_portrait_rules(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        style = "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,2,0,7,10,10,10,1"

        scaled = wrapper._scale_ass_style(style, 1.5, video_width=720, video_height=1280)

        assert ",30," in scaled  # fontsize 20 -> 30
        assert ",36,36,30," in scaled  # MarginL/R 至少 5% 宽度，MarginV 翻倍


class TestFFmpegWrapperSoftEmbedContracts:
    def test_embed_subtitle_soft_returns_false_when_video_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()

        result = wrapper.embed_subtitle_soft(tmp_path / "missing.mp4", tmp_path / "sub.srt", tmp_path / "out.mkv")

        assert result is False

    def test_embed_subtitle_soft_returns_false_when_subtitle_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        video.write_bytes(b"00")

        result = wrapper.embed_subtitle_soft(video, tmp_path / "missing.srt", tmp_path / "out.mkv")

        assert result is False

    def test_embed_subtitle_soft_mp4_uses_mov_text_and_succeeds(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        sub = tmp_path / "sub.ass"
        out = tmp_path / "out.mp4"
        video.write_bytes(b"00")
        sub.write_text("dummy", encoding="utf-8")
        calls = []

        def fake_run(cmd, capture_output, text, check):
            calls.append(cmd)
            out.write_bytes(b"11")
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr("subprocess.run", fake_run)

        result = wrapper.embed_subtitle_soft(video, sub, out)

        assert result is True
        assert "-c:s" in calls[0]
        assert "mov_text" in calls[0]

    def test_extract_audio_returns_false_when_input_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()

        assert wrapper.extract_audio(tmp_path / "missing.mp4", tmp_path / "audio.wav") is False

    def test_extract_audio_returns_true_when_ffmpeg_creates_output(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        audio = tmp_path / "audio.wav"
        video.write_bytes(b"00")

        def fake_run(cmd, capture_output, text, check):
            audio.write_bytes(b"11")
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr("subprocess.run", fake_run)

        assert wrapper.extract_audio(video, audio) is True

    def test_extract_audio_returns_false_on_ffmpeg_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        audio = tmp_path / "audio.wav"
        video.write_bytes(b"00")

        class _CalledProcessError(Exception):
            stderr = "failed"

        def fake_run(cmd, capture_output, text, check):
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr="failed")

        monkeypatch.setattr("subprocess.run", fake_run)

        assert wrapper.extract_audio(video, audio) is False


class TestFFmpegWrapperHardEmbedPlanning:
    def test_resolve_hard_embed_gpu_device_parses_explicit_cuda_device(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()

        gpu_id = wrapper._resolve_hard_embed_gpu_device("cuda:7")

        assert gpu_id == 7

    def test_resolve_hard_embed_gpu_device_selects_balanced_gpu_for_auto(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.select_gpu", lambda: 3)

        gpu_id = wrapper._resolve_hard_embed_gpu_device("auto")

        assert gpu_id == 3

    def test_resolve_hard_embed_gpu_device_raises_on_invalid_cuda_format(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()

        with pytest.raises(ValueError, match="无效的 GPU 设备格式"):
            wrapper._resolve_hard_embed_gpu_device("cuda:oops")

    def test_embed_subtitle_hard_delegates_gpu_device_planning_stage(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        sub = tmp_path / "sub.srt"
        out = tmp_path / "out.mp4"
        video.write_bytes(b"00")
        sub.write_text("dummy", encoding="utf-8")
        planned = []

        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.init", lambda max_per_gpu=5: None)
        monkeypatch.setattr(wrapper, "_resolve_hard_embed_gpu_device", lambda gpu_device: planned.append(gpu_device) or 5, raising=False)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.acquire", lambda gpu_id, timeout=600: True)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.release", lambda gpu_id: None)
        monkeypatch.setattr(wrapper, "_check_nvenc_support", lambda: True)
        monkeypatch.setattr(wrapper, "get_video_info", lambda _path: {"bit_rate": 1000})
        monkeypatch.setattr(wrapper, "_build_hard_embed_ffmpeg_command", lambda **kwargs: ["ffmpeg", str(kwargs["gpu_id"])], raising=False)
        monkeypatch.setattr(wrapper, "_run_ffmpeg_embed_process", lambda **kwargs: True, raising=False)

        result = wrapper.embed_subtitle_hard(video, sub, out, gpu_device="cuda:5")

        assert result is True
        assert planned == ["cuda:5"]

    def test_build_hard_embed_subtitle_filter_uses_ass_and_fontsdir_with_escaped_paths(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()

        vf = wrapper._build_hard_embed_subtitle_filter(
            subtitle_ext=".ass",
            processed_subtitle=Path("/tmp/C:/subs/demo.ass"),
            fonts_dir="/tmp/C:/fonts/demo",
        )

        assert vf == "ass='/tmp/C\\:/subs/demo.ass':fontsdir='/tmp/C\\:/fonts/demo'"

    def test_build_hard_embed_subtitle_filter_uses_subtitles_filter_for_non_ass(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()

        vf = wrapper._build_hard_embed_subtitle_filter(
            subtitle_ext=".srt",
            processed_subtitle=Path("/tmp/C:/subs/demo.srt"),
            fonts_dir="/tmp/C:/fonts/demo",
        )

        assert vf == "subtitles='/tmp/C\\:/subs/demo.srt'"

    def test_embed_subtitle_hard_delegates_subtitle_filter_planning_stage(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        sub = tmp_path / "sub.ass"
        out = tmp_path / "out.mp4"
        video.write_bytes(b"00")
        sub.write_text("dummy", encoding="utf-8")
        planned = []

        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.init", lambda max_per_gpu=5: None)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.select_gpu", lambda: 0)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.acquire", lambda gpu_id, timeout=600: True)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.release", lambda gpu_id: None)
        monkeypatch.setattr(wrapper, "_check_nvenc_support", lambda: True)
        monkeypatch.setattr(wrapper, "get_video_info", lambda _path: {"bit_rate": 1000})
        monkeypatch.setattr(
            wrapper,
            "_build_hard_embed_subtitle_filter",
            lambda **kwargs: planned.append(kwargs) or "ass='planned'",
            raising=False,
        )
        monkeypatch.setattr(
            wrapper,
            "_build_hard_embed_ffmpeg_command",
            lambda **kwargs: ["ffmpeg", kwargs["vf"]],
            raising=False,
        )
        monkeypatch.setattr(
            wrapper,
            "_run_ffmpeg_embed_process",
            lambda **kwargs: True,
            raising=False,
        )

        result = wrapper.embed_subtitle_hard(video, sub, out, gpu_device="auto", fonts_dir="/fonts")

        assert result is True
        assert planned == [{
            "subtitle_ext": ".ass",
            "processed_subtitle": sub,
            "fonts_dir": "/fonts",
        }]

    def test_build_hard_embed_ffmpeg_command_uses_hevc_nvenc_and_vbr_bitrate(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        out = tmp_path / "out.mp4"

        cmd = wrapper._build_hard_embed_ffmpeg_command(
            video_path=video,
            output_path=out,
            vf="subtitles='sub.srt'",
            video_codec="hevc",
            audio_codec="copy",
            crf=28,
            preset="p6",
            gpu_id=3,
            original_bitrate=1000,
        )

        assert cmd[:8] == [
            "ffmpeg",
            "-hwaccel",
            "cuda",
            "-hwaccel_device",
            "3",
            "-i",
            str(video),
            "-vf",
        ]
        assert "subtitles='sub.srt'" in cmd
        assert cmd[cmd.index("-c:v") + 1] == "hevc_nvenc"
        assert cmd[cmd.index("-rc") + 1] == "vbr"
        assert cmd[cmd.index("-cq") + 1] == "28"
        assert cmd[cmd.index("-b:v") + 1] == "1100"
        assert cmd[cmd.index("-maxrate") + 1] == "1500"
        assert cmd[cmd.index("-bufsize") + 1] == "3000"
        assert cmd[cmd.index("-preset") + 1] == "p6"
        assert cmd[cmd.index("-gpu") + 1] == "3"
        assert cmd[cmd.index("-c:a") + 1] == "copy"
        assert cmd[-2:] == ["-y", str(out)]

    def test_build_hard_embed_ffmpeg_command_falls_back_to_hevc_when_av1_nvenc_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        out = tmp_path / "out.mp4"
        monkeypatch.setattr(wrapper, "_check_encoder_support", lambda name: False)

        cmd = wrapper._build_hard_embed_ffmpeg_command(
            video_path=video,
            output_path=out,
            vf="subtitles='sub.srt'",
            video_codec="av1",
            audio_codec="aac",
            crf=23,
            preset="slow",
            gpu_id=0,
            original_bitrate=0,
        )

        assert cmd[cmd.index("-c:v") + 1] == "hevc_nvenc"
        assert cmd[cmd.index("-rc") + 1] == "constqp"
        assert cmd[cmd.index("-qp") + 1] == "23"
        assert cmd[cmd.index("-preset") + 1] == "p4"
        assert cmd[cmd.index("-gpu") + 1] == "0"
        assert cmd[cmd.index("-c:a") + 1] == "aac"

    def test_embed_subtitle_hard_delegates_command_planning_stage(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        sub = tmp_path / "sub.srt"
        out = tmp_path / "out.mp4"
        video.write_bytes(b"00")
        sub.write_text("dummy", encoding="utf-8")
        planned = []
        delegated = []

        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.init", lambda max_per_gpu=5: None)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.select_gpu", lambda: 0)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.acquire", lambda gpu_id, timeout=600: True)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.release", lambda gpu_id: None)
        monkeypatch.setattr(wrapper, "_check_nvenc_support", lambda: True)
        monkeypatch.setattr(wrapper, "get_video_info", lambda _path: {"bit_rate": 1000})
        monkeypatch.setattr(
            wrapper,
            "_build_hard_embed_ffmpeg_command",
            lambda **kwargs: planned.append(kwargs) or ["ffmpeg", "planned"],
            raising=False,
        )
        monkeypatch.setattr(
            wrapper,
            "_run_ffmpeg_embed_process",
            lambda **kwargs: delegated.append(kwargs) or True,
            raising=False,
        )

        result = wrapper.embed_subtitle_hard(video, sub, out, gpu_device="auto")

        assert result is True
        assert planned and planned[0]["original_bitrate"] == 1000
        assert planned[0]["gpu_id"] == 0
        assert delegated == [{"cmd": ["ffmpeg", "planned"], "output_path": out, "progress_callback": None}]


class TestFFmpegWrapperHardEmbedRuntime:
    def test_run_ffmpeg_embed_process_reports_progress_writes_log_and_succeeds(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        monkeypatch.setattr("time.sleep", lambda _seconds: None)
        monkeypatch.setattr("time.time", lambda: 10.0)
        wrapper = FFmpegWrapper()
        output = tmp_path / "out.mp4"
        callbacks = []

        class _FakeStderr:
            def __init__(self, lines, remaining=""):
                self._lines = list(lines)
                self._remaining = remaining

            def readline(self):
                return self._lines.pop(0) if self._lines else ""

            def read(self):
                return self._remaining

        class _FakeProcess:
            def __init__(self):
                self.stderr = _FakeStderr(
                    [
                        "Duration: 00:00:10.00\n",
                        "frame=1 time=00:00:05.00 bitrate=1000kbits/s\n",
                    ]
                )

            def poll(self):
                return None

            def wait(self):
                output.write_bytes(b"ok")
                return 0

        monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: _FakeProcess())

        result = wrapper._run_ffmpeg_embed_process(
            cmd=["ffmpeg", "-i", "input.mp4", str(output)],
            output_path=output,
            progress_callback=lambda progress, message: callbacks.append((progress, message)),
        )

        assert result is True
        assert callbacks[-1] == ("100", "合成完成")
        log_text = (output.parent / "ffmpeg_embed.log").read_text(encoding="utf-8")
        assert "=== FFmpeg 命令 ===" in log_text
        assert "Duration: 00:00:10.00" in log_text

    def test_run_ffmpeg_embed_process_returns_false_and_persists_failure_log(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        monkeypatch.setattr("time.sleep", lambda _seconds: None)
        wrapper = FFmpegWrapper()
        output = tmp_path / "out.mp4"

        class _FakeStderr:
            def __init__(self, lines, remaining=""):
                self._lines = list(lines)
                self._remaining = remaining

            def readline(self):
                return self._lines.pop(0) if self._lines else ""

            def read(self):
                return self._remaining

        class _FakeProcess:
            def __init__(self):
                self.stderr = _FakeStderr(
                    [
                        "Duration: 00:00:10.00\n",
                        "Error while filtering\n",
                        "failed to encode\n",
                    ],
                    remaining="fatal tail\n",
                )

            def poll(self):
                return None

            def wait(self):
                return 1

        monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: _FakeProcess())

        result = wrapper._run_ffmpeg_embed_process(
            cmd=["ffmpeg", "-i", "input.mp4", str(output)],
            output_path=output,
            progress_callback=None,
        )

        assert result is False
        log_text = (output.parent / "ffmpeg_embed.log").read_text(encoding="utf-8")
        assert "Error while filtering" in log_text
        assert "fatal tail" in log_text

    def test_embed_subtitle_hard_delegates_ffmpeg_execution_stage(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        sub = tmp_path / "sub.srt"
        out = tmp_path / "out.mp4"
        video.write_bytes(b"00")
        sub.write_text("dummy", encoding="utf-8")
        delegated = []
        released = []

        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.init", lambda max_per_gpu=5: None)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.select_gpu", lambda: 0)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.acquire", lambda gpu_id, timeout=600: True)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.release", lambda gpu_id: released.append(gpu_id))
        monkeypatch.setattr(wrapper, "_check_nvenc_support", lambda: True)
        monkeypatch.setattr(wrapper, "get_video_info", lambda _path: {"bit_rate": 1000})
        monkeypatch.setattr(
            wrapper,
            "_run_ffmpeg_embed_process",
            lambda **kwargs: delegated.append(kwargs) or True,
            raising=False,
        )
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should delegate to helper")),
        )

        result = wrapper.embed_subtitle_hard(video, sub, out, gpu_device="auto")

        assert result is True
        assert delegated and delegated[0]["output_path"] == out
        assert "ffmpeg" in delegated[0]["cmd"]
        assert released == [0]


class TestFFmpegWrapperHardEmbedContracts:
    def test_embed_subtitle_hard_rejects_cpu_gpu_device(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        sub = tmp_path / "sub.srt"
        out = tmp_path / "out.mp4"
        video.write_bytes(b"00")
        sub.write_text("dummy", encoding="utf-8")

        with pytest.raises(RuntimeError, match="禁止 CPU 回退"):
            wrapper.embed_subtitle_hard(video, sub, out, gpu_device="cpu")

    def test_embed_subtitle_hard_raises_when_nvenc_slot_acquire_times_out(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        sub = tmp_path / "sub.srt"
        out = tmp_path / "out.mp4"
        video.write_bytes(b"00")
        sub.write_text("dummy", encoding="utf-8")

        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.init", lambda max_per_gpu=5: None)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.select_gpu", lambda: 1)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.acquire", lambda gpu_id, timeout=600: False)

        with pytest.raises(RuntimeError, match="获取超时"):
            wrapper.embed_subtitle_hard(video, sub, out, gpu_device="auto")

    def test_embed_subtitle_hard_releases_session_when_nvenc_not_supported(self, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")
        wrapper = FFmpegWrapper()
        video = tmp_path / "video.mp4"
        sub = tmp_path / "sub.srt"
        out = tmp_path / "out.mp4"
        video.write_bytes(b"00")
        sub.write_text("dummy", encoding="utf-8")
        released = []

        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.init", lambda max_per_gpu=5: None)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.select_gpu", lambda: 2)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.acquire", lambda gpu_id, timeout=600: True)
        monkeypatch.setattr("vat.embedder.ffmpeg_wrapper._nvenc_manager.release", lambda gpu_id: released.append(gpu_id))
        monkeypatch.setattr(wrapper, "_check_nvenc_support", lambda: False)

        with pytest.raises(RuntimeError, match="不支持 NVENC"):
            wrapper.embed_subtitle_hard(video, sub, out, gpu_device="auto")

        assert released == [2]
