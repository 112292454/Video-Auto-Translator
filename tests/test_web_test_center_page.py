"""web 测试中心页面契约测试。"""

from fastapi.testclient import TestClient


def test_test_center_page_renders_with_expected_sections():
    from vat.web import app as web_app_module

    with TestClient(web_app_module.app) as client:
        response = client.get("/test")

    assert response.status_code == 200
    assert "测试中心" in response.text
    assert "在这里检查接口、提示词、配置和运行环境。" in response.text
    assert "接口测试" in response.text
    assert "全部测试" in response.text
    assert "Prompt 预览" in response.text
    assert "测试输入" in response.text
    assert "配置编辑" in response.text
    assert "Upload 配置" in response.text
    assert "结构化编辑" in response.text
    assert "环境检测" in response.text
    assert "如何复刻这个请求" in response.text
    assert "联调方式" not in response.text
    assert "这里重点看 model / proxy / messages" not in response.text


def test_base_navigation_contains_test_entry():
    from vat.web import app as web_app_module

    with TestClient(web_app_module.app) as client:
        response = client.get("/test")

    assert response.status_code == 200
    assert '>🧪 测试<' in response.text
