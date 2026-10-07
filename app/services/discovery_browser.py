"""Compatibility adapter: anonymous discovery never accepts stored identity state."""

from app.services.browser_discovery import BrowserDiscovery


class AnonymousDiscoveryBrowser(BrowserDiscovery):
    def collect(
        self,
        start_url,
        options,
        before_request,
        on_response,
        on_candidate,
        seed_urls=None,
        on_attempt=None,
    ):
        return super().collect(
            start_url, options, before_request, on_response, on_candidate, seed_urls, on_attempt
        )
