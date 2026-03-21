"""上传模板渲染契约测试。"""

from types import SimpleNamespace

from vat.uploaders.template import TemplateRenderer, build_upload_context, render_upload_metadata, _format_duration


def _make_video(metadata=None, title="原标题"):
    return SimpleNamespace(
        id="vid1",
        source_url="https://youtube.com/watch?v=vid1",
        title=title,
        metadata=metadata or {},
    )


class TestTemplateRenderer:
    def test_render_empty_template_returns_empty_string(self):
        renderer = TemplateRenderer()

        assert renderer.render("", {"title": "x"}) == ""

    def test_render_substitutes_context_and_custom_vars(self):
        renderer = TemplateRenderer({"site": "B站"})

        result = renderer.render("【${site}】${title}", {"title": "翻译标题"})

        assert result == "【B站】翻译标题"

    def test_render_keeps_unknown_variable_literal(self):
        renderer = TemplateRenderer()

        result = renderer.render("${missing}", {})

        assert result == "${missing}"

    def test_get_available_vars_merges_custom_and_context(self):
        renderer = TemplateRenderer({"site": "B站"})

        vars_list = renderer.get_available_vars({"title": "翻译标题"})

        assert vars_list == ["site", "title"]


class TestBuildUploadContext:
    def test_build_upload_context_uses_custom_uploader_name_from_playlist(self):
        video = _make_video({
            "uploader": "原频道",
            "translated": {"title_translated": "翻译标题", "description_translated": "翻译简介"},
        })

        context = build_upload_context(
            video,
            playlist_info={"name": "Playlist", "index": 5, "id": "PL1", "uploader_name": "白上吹雪"},
        )

        assert context["uploader_name"] == "白上吹雪"
        assert context["channel_name"] == "白上吹雪"
        assert context["playlist_index"] == 5

    def test_build_upload_context_keeps_translated_fields_empty_without_fallback_to_original(self):
        video = _make_video({"uploader": "原频道"})

        context = build_upload_context(video)

        assert context["translated_title"] == ""
        assert context["translated_desc"] == ""

    def test_build_upload_context_extracts_stage_model_names(self):
        video = _make_video({
            "stage_models": {
                "split": {"model": "split-model"},
                "optimize": {"model": "opt-model"},
                "translate": {"model": "trans-model"},
            }
        })

        context = build_upload_context(video)

        assert context["split_model"] == "split-model"
        assert context["optimize_model"] == "opt-model"
        assert context["translate_model"] == "trans-model"
        assert context["models_summary"] == "split-model、opt-model、trans-model"

    def test_build_upload_context_uses_channel_url_when_channel_id_present(self):
        video = _make_video({
            "channel_id": "UC123",
            "uploader": "原频道",
        })

        context = build_upload_context(video)

        assert context["channel_url"] == "https://www.youtube.com/channel/UC123"

    def test_build_upload_context_keeps_invalid_upload_date_unformatted(self):
        video = _make_video({
            "upload_date": "2025-01",
        })

        context = build_upload_context(video)

        assert context["original_date_raw"] == "2025-01"
        assert context["original_date"] == ""

    def test_build_upload_context_defaults_playlist_fields_when_playlist_missing(self):
        video = _make_video({"uploader": "原频道"})

        context = build_upload_context(video, None)

        assert context["playlist_name"] == ""
        assert context["playlist_index"] == ""
        assert context["playlist_id"] == ""
        assert context["uploader_name"] == "原频道"

    def test_build_upload_context_playlist_info_without_index_falls_back_to_empty_string(self):
        video = _make_video({"uploader": "原频道"})

        context = build_upload_context(video, {"name": "PL", "id": "PL1"})

        assert context["playlist_index"] == ""

    def test_build_upload_context_extra_vars_override_same_name_context(self):
        video = _make_video({"uploader": "原频道"})

        context = build_upload_context(video, extra_vars={"channel_name": "覆盖后的频道"})

        assert context["channel_name"] == "覆盖后的频道"


class TestRenderUploadMetadata:
    def test_render_upload_metadata_renders_title_and_description(self):
        video = _make_video({
            "translated": {
                "title_translated": "翻译标题",
                "description_translated": "翻译简介",
            },
            "uploader": "原频道",
        })

        rendered = render_upload_metadata(
            video,
            {
                "title": "【${uploader_name}】${translated_title}",
                "description": "${translated_desc}",
                "custom_vars": {},
            },
            playlist_info={"name": "Playlist", "index": 3, "id": "PL1", "uploader_name": "白上吹雪"},
        )

        assert rendered["title"] == "【白上吹雪】翻译标题"
        assert rendered["description"] == "翻译简介"
        assert rendered["context"]["playlist_name"] == "Playlist"

    def test_render_upload_metadata_uses_default_templates_when_missing(self):
        video = _make_video({
            "translated": {
                "title_translated": "翻译标题",
                "description_translated": "翻译简介",
            }
        })

        rendered = render_upload_metadata(video, {}, None)

        assert rendered["title"] == "翻译标题"
        assert rendered["description"] == "翻译简介"

    def test_render_upload_metadata_context_overrides_custom_vars(self):
        video = _make_video({
            "translated": {"title_translated": "翻译标题"},
            "uploader": "原频道",
        })

        rendered = render_upload_metadata(
            video,
            {
                "title": "【${channel_name}】${translated_title}",
                "description": "",
                "custom_vars": {"channel_name": "自定义频道"},
            },
            None,
            {"channel_name": "覆盖频道"},
        )

        assert rendered["title"] == "【覆盖频道】翻译标题"


class TestTemplateDurationFormatting:
    def test_format_duration_handles_hours_and_minutes(self):
        assert _format_duration(3725) == "1:02:05"

    def test_format_duration_handles_minutes_only(self):
        assert _format_duration(125) == "2:05"
