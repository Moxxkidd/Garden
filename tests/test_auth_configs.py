from app.schemas.auth import HttpLoginConfig, PlaywrightLoginConfig
from app.services.login_configs import LoginConfigService


def test_http_login_config_parsing() -> None:
    config = LoginConfigService().load("examples/login/demo-http-admin.yaml")
    assert isinstance(config, HttpLoginConfig)
    assert config.login_request.url == "/demo/auth/http/login"
    assert config.refresh_request is not None


def test_playwright_login_config_parsing() -> None:
    config = LoginConfigService().load("examples/login/demo-playwright-admin.yaml")
    assert isinstance(config, PlaywrightLoginConfig)
    assert config.login_url == "/demo/auth/ui/login"
    assert config.refresh_via_relogin is True
