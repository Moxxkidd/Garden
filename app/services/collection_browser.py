"""Durable navigation frontier and CDP request accounting for browser collectors."""

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models.collection_queue import CollectionAttempt, CollectionGate, CollectionWork
from app.services.collection_runtime import CollectionRuntime, CollectionStopped
from app.services.collection_storage import digest
from app.services.discovery import origin
from app.services.discovery_sources import parse_sitemap
from app.services.projects import project_url


class CollectionBrowser(CollectionRuntime):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.network_attempts = {}
        self.navigation = None
        self.sitemap_requests = 0
        self.observed_urls = set()

    def enqueue(self, url, depth=0, method="GET", asset_type="page", source="browser_navigation"):
        if not self.accepts(url, method):
            return False
        work = self.queues.enqueue(
            self.session,
            self.queue_id,
            url,
            depth=depth,
            method=method,
            asset_type=asset_type,
            source=source,
            max_depth=self.options.max_depth,
        )
        self.session.commit()
        return work["id"] is not None and work["state"] not in {"suppressed", "failed"}

    def next_navigation(self):
        while True:
            self.check()
            self.navigation = self.queues.claim(self.session, self.queue_id, self.token)
            self.session.commit()
            if self.navigation is not None:
                break
            pending_time = self.session.scalar(
                select(CollectionWork.not_before)
                .where(
                    CollectionWork.queue_id == self.queue_id,
                    CollectionWork.state == "deferred",
                )
                .order_by(CollectionWork.not_before)
                .limit(1)
            )
            if pending_time is None:
                return None
            self.session.commit()
            self.wait_until(pending_time)
        if self.navigation["asset_type"] == "sitemap":
            if (
                self.sitemap_requests >= self.options.max_sitemap_documents
                or self.navigation["depth"] > self.options.max_sitemap_depth
            ):
                raise CollectionStopped("max_sitemap_documents")
            self.sitemap_requests += 1
        request = self.queues.request(self.session, self.queue_id, self.navigation["id"])
        return request["url"], self.navigation["depth"], request["method"]

    def sitemap_candidates(self, result):
        if self.navigation is None or self.navigation["asset_type"] != "sitemap":
            return []
        request = self.queues.request(self.session, self.queue_id, self.navigation["id"])
        if project_url(result.requested_url) != request["url"]:
            return []
        if result.status_code != 200 or result.body_truncated:
            raise CollectionStopped("sitemap_incomplete")
        candidates, indexes = parse_sitemap(
            result.final_url, result.body_text, limit=self.options.max_candidates
        )
        for child in indexes:
            if origin(child) != origin(result.final_url):
                raise CollectionStopped("sitemap_outside_scope")
            self.enqueue(
                child, self.navigation["depth"] + 1, source="sitemap_index", asset_type="sitemap"
            )
        for item in candidates:
            if item.get("auto_visit", True) and origin(item["url"]) == origin(result.final_url):
                self.enqueue(item["url"], source="sitemap", asset_type=item["asset_type"])
        return candidates

    def sending(self, event, route_url):
        request = event["request"]
        url, method = project_url(request["url"]), request["method"].upper()
        if not event.get("networkId"):
            raise ConflictError("collection_request_unattributed")
        work = self.queues.enqueue(
            self.session,
            self.queue_id,
            url,
            method=method,
            asset_type="page" if event.get("resourceType") == "Document" else "resource",
            source="browser_request",
        )
        if work["id"] is None or work["state"] == "suppressed":
            raise ConflictError("collection_max_work")
        work = self.queues.claim_requested(self.session, self.queue_id, work["id"], self.token)
        self.session.commit()
        self.work_id = work["id"] if work["state"] == "leased" else None
        self.retrying = bool(
            self.navigation
            and self.navigation["id"] == work["id"]
            and self.navigation["not_before"] > 0
        )
        attempt = self.before_send(url, method, kind="browser")
        self.work_id = None
        network_id = event.get("networkId")
        if network_id:
            self.network_attempts[network_id] = attempt
        return attempt

    def network_response(self, event):
        attempt = self.network_attempts.get(event["requestId"])
        if attempt:
            response = event.get("response") or event.get("redirectResponse")
            if response:
                headers = {k.lower(): v for k, v in response.get("headers", {}).items()}
                self.admission.finish(
                    self.session,
                    attempt,
                    self.token,
                    status_code=int(response["status"]),
                    retry_after=headers.get("retry-after"),
                )
                self.session.commit()

    def network_failed(self, event):
        attempt = self.network_attempts.get(event["requestId"])
        if attempt:
            self.admission.finish(self.session, attempt, self.token)
            record = self.session.get(CollectionAttempt, attempt)
            work = self.session.get(CollectionWork, record.work_id) if record.work_id else None
            if record.state == "failed" and work is not None and work.state == "leased":
                gate = self.session.get(
                    CollectionGate, self.queues.row(self.session, self.queue_id).origin
                )
                self.queues.stop_work(
                    self.session,
                    self.queue_id,
                    work.id,
                    self.token,
                    "transport_error",
                    retry_at=gate.cooldown_until
                    if not event.get("canceled")
                    and work.attempts <= self.options.durable.retry_attempts
                    else None,
                )
            self.session.commit()

    def persist(self, result, depth=0, asset_type=None, **evidence_options):
        key = digest([project_url(result.final_url), result.method])
        self.latest_attempt = result._collection_attempt_id
        if self.latest_attempt is None:
            # A cached/unattributed response cannot be invented as a sent request.
            raise ConflictError("collection_response_unattributed")
        attempt = self.session.get(CollectionAttempt, self.latest_attempt)
        if attempt.queue_id != self.queue_id or attempt.request_key != key:
            raise ConflictError("collection_response_unattributed")
        if attempt.state == "possibly_sent":
            self.response(result.status_code, result.headers.get("retry-after"))
        asset = super().persist(result, depth, asset_type, **evidence_options)
        work = self.session.scalar(
            select(CollectionWork).where(
                CollectionWork.queue_id == self.queue_id, CollectionWork.request_key == key
            )
        )
        retryable = result.status_code in {429, 502, 503, 504}
        if work and (
            work.state == "leased"
            or (
                not retryable
                and attempt.work_id == work.id
                and work.state in {"failed", "deferred"}
            )
        ):
            if retryable:
                gate = self.session.get(
                    CollectionGate, self.queues.row(self.session, self.queue_id).origin
                )
                self.queues.stop_work(
                    self.session,
                    self.queue_id,
                    work.id,
                    self.token,
                    "retryable_response",
                    retry_at=gate.cooldown_until
                    if work.attempts <= self.options.durable.retry_attempts
                    else None,
                )
            else:
                self.queues.complete(
                    self.session, self.queue_id, work.id, self.token, attempt_id=attempt.id
                )
        if not retryable:
            self.observed_urls.add(project_url(result.final_url))
        return asset

    def assess_identity(self, url, method, assessment):
        self.queues.assess_identity(
            self.session, self.queue_id, self.token, url, method, assessment
        )

    def navigation_finished(self, success, final_url=None):
        if self.navigation is None:
            return
        row = self.session.get(CollectionWork, self.navigation["id"], populate_existing=True)
        if row.state == "leased" and success and final_url:
            # DOM navigation proves a route view only when its backing document was captured.
            document = project_url(final_url).split("#", 1)[0]
            if document in self.observed_urls:
                self.queues.complete(self.session, self.queue_id, row.id, self.token)
                self.session.commit()
        elif row.state == "leased" and not success:
            self.queues.stop_work(
                self.session, self.queue_id, row.id, self.token, "navigation_error"
            )
            self.session.commit()
