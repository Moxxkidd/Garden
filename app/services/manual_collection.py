"""Worker-local controls for an explicitly requested manual browser window."""

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event


@dataclass
class ManualCollectionControl:
    begin: Event = field(default_factory=Event)
    finish: Event = field(default_factory=Event)
    headless: bool = False
    on_open: Callable | None = None
    state: object = None
    failure: Exception | None = None
    settling: bool = False
    login_requests: int = 0
