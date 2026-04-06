"""Placeholder proxy abstraction for future auth and traffic integrations."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProxySettings:
    enabled: bool = False
    http_proxy: str | None = None
    https_proxy: str | None = None
