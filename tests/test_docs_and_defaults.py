"""文档与默认值一致性测试。"""

from pathlib import Path
import re

import yaml

from vat.config import Config


REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_REPO_URL = "https://github.com/" "ZeyuanGuo/Video-Auto-Translator"
CANONICAL_REPO_CLONE_URL = f"{CANONICAL_REPO_URL}.git"
CANONICAL_REPO_CLONE_COMMAND = f"git clone {CANONICAL_REPO_CLONE_URL} vat && cd vat"


def _extract_between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0]


class TestWebPortConsistency:
    def test_config_from_dict_web_port_default_matches_default_yaml(self):
        default_yaml = yaml.safe_load((REPO_ROOT / "config" / "default.yaml").read_text(encoding="utf-8"))
        partial = dict(default_yaml)
        partial.pop("web")
        config = Config.from_dict(partial)

        assert default_yaml["web"]["port"] == 13579
        assert config.web.port == default_yaml["web"]["port"]

    def test_vat_web_readme_startup_example_uses_default_port(self):
        web_readme = (REPO_ROOT / "vat" / "web" / "readme.md").read_text(encoding="utf-8")
        startup_section = web_readme.split("## 7. 启动方式", 1)[1]
        startup_example = startup_section.split("```bash", 1)[1].split("```", 1)[0]

        assert "--port 13579" in startup_example
        assert "--port 8000" not in startup_example

    def test_readme_default_port_matches_config(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "默认端口 13579" in readme
        assert "默认端口 8094" not in readme

    def test_readme_en_default_port_matches_config(self):
        readme_en = (REPO_ROOT / "README_EN.md").read_text(encoding="utf-8")
        assert "default port 13579" in readme_en
        assert "default port 8094" not in readme_en

    def test_webui_manual_default_port_matches_config(self):
        manual = (REPO_ROOT / "docs" / "webui_manual.md").read_text(encoding="utf-8")
        assert "http://localhost:13579" in manual
        assert "默认运行在 http://localhost:8080" not in manual

    def test_readmes_keep_webui_test_center_as_self_check_entry(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        readme_en = (REPO_ROOT / "README_EN.md").read_text(encoding="utf-8")

        webui_section = readme.split("## Web 管理界面", 1)[1].split("```bash", 1)[0]
        webui_en_section = readme_en.split("## Web UI", 1)[1].split("```bash", 1)[0]
        webui_en_section_lower = webui_en_section.lower()

        assert "/test" in webui_section
        assert "测试中心" in webui_section
        assert "自检" in webui_section
        assert "首次部署" in webui_section
        assert "配置" in webui_section
        assert "环境" in webui_section

        assert "/test" in webui_en_section
        assert "test center" in webui_en_section_lower
        assert "self-check" in webui_en_section_lower
        assert "first deployment" in webui_en_section_lower
        assert "configuration" in webui_en_section_lower
        assert "environment" in webui_en_section_lower


class TestReadmeConfigEntryConsistency:
    def test_readmes_keep_config_edit_entry_separate_from_default_reference(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        readme_en = (REPO_ROOT / "README_EN.md").read_text(encoding="utf-8")

        config_section = _extract_between(readme, "### 配置", "\n---")
        config_en_section = _extract_between(readme_en, "### Configuration", "\n---")

        config_entry_lines = [
            line.strip()
            for line in config_section.splitlines()
            if "config/config.yaml" in line and "config/default.yaml" in line
        ]
        config_entry_en_lines = [
            line.strip()
            for line in config_en_section.splitlines()
            if "config/config.yaml" in line and "config/default.yaml" in line
        ]

        assert len(config_entry_lines) == 1
        assert len(config_entry_en_lines) == 1

        zh_line = config_entry_lines[0]
        en_line = config_entry_en_lines[0]

        assert re.fullmatch(
            r"日常编辑请优先使用\s*\[`config/config\.yaml`\]\(config/config\.yaml\)；\s*"
            r"\[`config/default\.yaml`\]\(config/default\.yaml\)\s*仅作为完整字段与注释参考。",
            zh_line,
        ) is not None
        assert re.search(
            r"优先使用\s*\[`config/default\.yaml`\]\(config/default\.yaml\)",
            zh_line,
        ) is None
        assert re.search(
            r"\[`config/config\.yaml`\]\(config/config\.yaml\)\s*仅作为完整字段与注释参考",
            zh_line,
        ) is None

        assert re.fullmatch(
            r"For day-to-day edits,\s*use\s*\[`config/config\.yaml`\]\(config/config\.yaml\)\s*first;\s*"
            r"\[`config/default\.yaml`\]\(config/default\.yaml\)\s*is only a full field/comment reference\.",
            en_line,
        ) is not None
        assert re.search(
            r"use\s*\[`config/default\.yaml`\]\(config/default\.yaml\)\s*first",
            en_line,
            re.IGNORECASE,
        ) is None
        assert re.search(
            r"\[`config/config\.yaml`\]\(config/config\.yaml\)\s*is only a full field/comment reference",
            en_line,
            re.IGNORECASE,
        ) is None


class TestReadmeDeepModuleDocEntries:
    def test_readmes_project_structure_section_exposes_deep_module_doc_links(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        readme_en = (REPO_ROOT / "README_EN.md").read_text(encoding="utf-8")

        project_section = _extract_between(readme, "## 项目结构", "\n---")
        project_en_section = _extract_between(readme_en, "## Project Structure", "\n---")

        required_links = [
            "(vat/asr/readme.md)",
            "(vat/embedder/readme.md)",
            "(vat/translator/readme.md)",
            "(vat/uploaders/readme.md)",
            "(vat/pipeline/readme.md)",
            "(vat/llm/readme.md)",
            "(vat/downloaders/readme.md)",
            "(vat/subtitle_utils/readme.md)",
            "(vat/utils/readme.md)",
        ]

        for link in required_links:
            assert link in project_section
            assert link in project_en_section


class TestWatchDefaultsConsistency:
    def test_watch_defaults_match_default_yaml(self):
        default_yaml = yaml.safe_load((REPO_ROOT / "config" / "default.yaml").read_text(encoding="utf-8"))
        partial = dict(default_yaml)
        partial.pop("watch")
        config = Config.from_dict(partial)

        assert config.watch.default_interval == default_yaml["watch"]["default_interval"]
        assert config.watch.default_concurrency == default_yaml["watch"]["default_concurrency"]


class TestPackagingMetadataConsistency:
    def test_setup_py_repository_url_matches_canonical_repo(self):
        setup_py = (REPO_ROOT / "setup.py").read_text(encoding="utf-8")
        match = re.search(r'url\s*=\s*"([^"]+)"', setup_py)

        assert "https://github.com/yourusername/VAT" not in setup_py
        assert match is not None
        assert match.group(1) == CANONICAL_REPO_URL


class TestRepoUrlConstantDefinitions:
    def test_canonical_repo_url_constants_are_centralized(self):
        source = Path(__file__).read_text(encoding="utf-8")
        canonical_assignment = 'CANONICAL_REPO_URL = ' + '"https://github.com/" "ZeyuanGuo/Video-Auto-Translator"'
        clone_url_assignment = 'CANONICAL_REPO_CLONE_URL = f"{' + 'CANONICAL_REPO_URL' + '}.git"'
        clone_command_assignment = 'CANONICAL_REPO_CLONE_COMMAND = f"git clone {' + 'CANONICAL_REPO_CLONE_URL' + '} vat && cd vat"'
        setup_assertion = 'assert match.group(1) == ' + 'CANONICAL_REPO_URL'
        readme_assertion = 'assert ' + 'CANONICAL_REPO_CLONE_COMMAND' + ' in readme\n'
        readme_en_assertion = 'assert ' + 'CANONICAL_REPO_CLONE_COMMAND' + ' in readme_en\n'

        assert source.count(canonical_assignment) == 1
        assert source.count(clone_url_assignment) == 1
        assert source.count(clone_command_assignment) == 1
        assert source.count(setup_assertion) == 1
        assert source.count(readme_assertion) == 1
        assert source.count(readme_en_assertion) == 1




class TestReadmeCloneUrlConsistency:
    def test_readme_clone_example_uses_canonical_repo_url(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        assert "git clone <repo-url> && cd vat" not in readme
        assert f"git clone {CANONICAL_REPO_CLONE_URL} && cd vat" not in readme
        assert CANONICAL_REPO_CLONE_COMMAND in readme

    def test_readme_en_clone_example_uses_canonical_repo_url(self):
        readme_en = (REPO_ROOT / "README_EN.md").read_text(encoding="utf-8")

        assert "git clone <repo-url> && cd vat" not in readme_en
        assert f"git clone {CANONICAL_REPO_CLONE_URL} && cd vat" not in readme_en
        assert CANONICAL_REPO_CLONE_COMMAND in readme_en

    def test_from_scratch_quickstart_uses_canonical_clone_command(self):
        quickstart = (REPO_ROOT / "docs" / "from_scratch_quickstart.md").read_text(encoding="utf-8")

        assert f"git clone {CANONICAL_REPO_CLONE_URL} && cd vat" not in quickstart
        assert CANONICAL_REPO_CLONE_COMMAND in quickstart
        assert "vat init" in quickstart
        assert 'vat pipeline --url "$(realpath ./demo.mp4)" --title "demo"' in quickstart
        assert "vat tools test-center --kind ffmpeg" in quickstart
        assert "video_codec: libx265" in quickstart
        assert "video_codec: h264_nvenc" in quickstart


class TestRuntimeDependencyConsistency:
    def test_requirements_include_subtitle_font_runtime_dependencies(self):
        requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")

        assert re.search(r"^Pillow[>=<~=!]", requirements, re.MULTILINE)
        assert re.search(r"^fonttools[>=<~=!]", requirements, re.MULTILINE)


class TestLicenseConsistency:
    def test_repo_root_license_exists_and_is_gplv3(self):
        license_file = REPO_ROOT / "LICENSE"

        assert license_file.exists()

        license_text = license_file.read_text(encoding="utf-8")
        assert "GNU GENERAL PUBLIC LICENSE" in license_text
        assert "Version 3, 29 June 2007" in license_text
