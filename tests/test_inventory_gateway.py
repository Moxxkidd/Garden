from app.integrations.inventory.playwright_gateway import SyncPlaywrightInventoryGateway
from app.schemas.inventory import InventoryBuildControls


class _FakePage:
    def __init__(self, hrefs):
        self._hrefs = hrefs

    def eval_on_selector_all(self, selector, script):
        assert selector == "a[href]"
        return self._hrefs


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
