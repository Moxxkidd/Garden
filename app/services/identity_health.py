"""Transport-independent request admission and bounded identity evidence batches."""

from time import monotonic
from urllib.parse import urlsplit

from app.schemas.identity import SessionHealthView


class IdentityHealthStopped(RuntimeError):
    """No further target sends are allowed for this identity context."""


class IdentityHealthGate:
    def __init__(
        self,
        *,
        session_id,
        request_budget,
        validate,
        persist,
        clock=monotonic,
        admission=lambda: None,
        login_url=None,
    ):
        self.session_id = session_id
        self.request_budget = request_budget
        self.validate = validate
        self.persist = persist
        self.clock = clock
        self.admission = admission
        self.login_url = login_url
        self.request_count = 0
        self.business_since_check = 0
        self.last_checked = clock()
        self.health = SessionHealthView(session_id=session_id, status="unknown")
        self.reason_code = None
        self.pending = []
        self.started = False

    def _stop(self, reason):
        self.reason_code = reason
        if self.pending:
            pending, self.pending = self.pending, []
            self.persist(pending, "identity_uncertain")
        raise IdentityHealthStopped(reason)

    def _admit(self):
        if self.reason_code:
            raise IdentityHealthStopped(self.reason_code)
        self.admission()
        if self.request_count >= self.request_budget:
            self.health = SessionHealthView(session_id=self.session_id, status="unknown")
            self.reason_code = "max_browser_requests"
            raise IdentityHealthStopped(self.reason_code)
        self.request_count += 1

    def _check(self):
        if self.reason_code:
            raise IdentityHealthStopped(self.reason_code)
        try:
            self.health = self.validate(self._admit)
            self.admission()  # Recheck cancellation/revocation after the proof.
        except IdentityHealthStopped as error:
            self.health = SessionHealthView(session_id=self.session_id, status="unknown")
            self._stop(self.reason_code or str(error))
        except Exception:
            self.health = SessionHealthView(session_id=self.session_id, status="unknown")
            self._stop("identity_validation_interrupted")
        self.started = True
        self.last_checked = self.clock()
        self.business_since_check = 0
        if self.health.status != "ready":
            self._stop(self.health.reason_code or "identity_validation_failed")
        if self.pending:
            pending, self.pending = self.pending, []
            self.persist(pending, "confirmed")
        return self.health

    def before_send(self):
        if (
            not self.started
            or self.business_since_check >= 20
            or self.clock() - self.last_checked >= 30
        ):
            self._check()
        try:
            self._admit()
        except IdentityHealthStopped as error:
            self._stop(self.reason_code or str(error))
        self.business_since_check += 1

    def observe(self, result, observation):
        diagnostic = bool(self.login_url) and (
            urlsplit(result.final_url)._replace(query="", fragment="")
            == urlsplit(self.login_url)._replace(query="", fragment="")
        )
        if diagnostic:
            self.persist([observation], "auth_diagnostic")
        else:
            self.pending.append(observation)
        if diagnostic or result.status_code in {401, 403} or self.business_since_check >= 20:
            self._check()

    def finish(self):
        return self._check()
