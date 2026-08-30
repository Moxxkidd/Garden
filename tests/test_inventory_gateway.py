from app.integrations.inventory.playwright_gateway import SyncPlaywrightInventoryGateway
from app.schemas.inventory import InventoryBuildControls


class _FakePage:
    def __init__(self, hrefs):
        self._hrefs = hrefs

    def eval_on_selector_all(self, selector, script):
        assert selector == "a[href]"
        return self._hrefs


class _FakeResponse:
    def __init__(self, *, content_length: str | None, text: str = "body") -> None:
        self.content_length = content_length
        self.body = text
        self.text_calls = 0

    def header_value(self, name: str) -> str | None:
        if name == "content-type":
            return "application/json"
        if name == "content-length":
            return self.content_length
        return None

    def text(self) -> str:
        self.text_calls += 1
        return self.body


def test_extract_links_skips_download_targets() -> None:
    gateway = SyncPlaywrightInventoryGateway()
    page = _FakePage(
        [
            "http://127.0.0.1:8081/index.php",
            "http://127.0.0.1:8081/docs/DVWA_v1.3.pdf",
            "http://127.0.0.1:8081/security.php",
        ]
    )

    links = gateway._extract_links(
        page,
        "http://127.0.0.1:8081",
        InventoryBuildControls(),
    )

    assert "http://127.0.0.1:8081/index.php" in links
    assert "http://127.0.0.1:8081/security.php" in links
    assert "http://127.0.0.1:8081/docs/DVWA_v1.3.pdf" not in links


def test_download_navigation_errors_are_classified() -> None:
    gateway = SyncPlaywrightInventoryGateway()

    assert gateway._is_download_navigation_error(Exception("Page.goto: Download is starting"))
    assert gateway._is_download_navigation_error(Exception("page.goto: download is starting"))
    assert gateway._is_download_navigation_error(Exception("Download is starting"))
    assert not gateway._is_download_navigation_error(Exception("Navigation timeout"))


def test_response_text_is_not_loaded_when_declared_body_exceeds_limit() -> None:
    gateway = SyncPlaywrightInventoryGateway()
    response = _FakeResponse(content_length=str(256 * 1024 + 1))

    assert gateway._extract_navigation_response_text(response) is None
    assert gateway._extract_response_text(response, "application/json") is None
    assert response.text_calls == 0


def test_response_text_is_not_loaded_when_size_is_unknown() -> None:
    gateway = SyncPlaywrightInventoryGateway()
    response = _FakeResponse(content_length=None)

    assert gateway._extract_navigation_response_text(response) is None
    assert gateway._extract_response_text(response, "application/json") is None
    assert response.text_calls == 0


def test_response_text_is_loaded_when_declared_body_is_within_limit() -> None:
    gateway = SyncPlaywrightInventoryGateway()
    response = _FakeResponse(content_length="4", text="body")

    assert gateway._extract_navigation_response_text(response) == "body"
    assert gateway._extract_response_text(response, "application/json") == "body"
    assert response.text_calls == 2
