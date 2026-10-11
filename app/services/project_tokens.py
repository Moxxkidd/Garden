"""Bounded ephemeral confirmations; DB revisions remain the write authority."""

from secrets import token_urlsafe
from threading import RLock
from time import monotonic

from app.core.errors import ConflictError


class ProjectTokens:
    def __init__(self, clock=monotonic):
        self.clock = clock
        self.values = {}
        self.lock = RLock()

    def put(self, value):
        with self.lock:
            self.values = {k: v for k, v in self.values.items() if v[0] > self.clock()}
            if len(self.values) >= 100:
                raise ConflictError("too_many_project_previews")
            token = token_urlsafe(32)
            self.values[token] = (self.clock() + 900, value)
            return token

    def get(self, token):
        with self.lock:
            item = self.values.get(token)
            if not item or item[0] <= self.clock():
                raise ConflictError("project_preview_expired")
            return item[1]
