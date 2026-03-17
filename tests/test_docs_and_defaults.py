"""文档与默认值一致性测试。"""

from pathlib import Path
import re

import yaml

from vat.config import Config


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestWebPortConsistency:
    def test_config_from_dict_web_port_default_matches_default_yaml(self):
        default_yaml = yaml.safe_load((REPO_ROOT / "config" / "default.yaml").read_text(encoding="utf-8"))
        partial = dict(default_yaml)
        partial.pop("web")
        config = Config.from_dict(partial)

        assert default_yaml["web"]["port"] == 13579
        assert config.web.port == default_yaml["web"]["port"]

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


class TestWatchDefaultsConsistency:
    def test_watch_defaults_match_default_yaml(self):
        default_yaml = yaml.safe_load((REPO_ROOT / "config" / "default.yaml").read_text(encoding="utf-8"))
        partial = dict(default_yaml)
        partial.pop("watch")
        config = Config.from_dict(partial)

        assert config.watch.default_interval == default_yaml["watch"]["default_interval"]
        assert config.watch.default_concurrency == default_yaml["watch"]["default_concurrency"]
