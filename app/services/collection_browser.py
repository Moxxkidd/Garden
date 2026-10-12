"""Durable navigation frontier and CDP request accounting for browser collectors."""

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models.collection_queue import CollectionAttempt, CollectionWork
from app.services.collection_runtime import CollectionRuntime, CollectionStopped
from app.services.collection_storage import digest
from app.services.discovery import origin
from app.services.discovery_sources import parse_sitemap
from app.services.projects import project_url


class CollectionBrowser(CollectionRuntime):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.network_attempts = {}
        self.url_attempts = {}
        self.navigation = None
        self.sitemap_requests = 0
        self.observed_urls = set()

    def enqueue(self, url, depth=0, method="GET", asset_type="page", source="browser_navigation"):
        work = self.queues.enqueue(
            self.session,
            self.queue_id,
            url,
            depth=depth,
            method=method,
            asset_type=asset_type,
            source=source,
        )
        if work["id"] and depth > self.options.max_depth:
            row = self.session.get(CollectionWork, work["id"])
            if row.state == "pending":
                row.state, row.reason = "suppressed", "max_depth"
        self.session.commit()
        return work["id"] is not None and work["state"] not in {"suppressed", "failed"}

    def next_navigation(self):
        self.check()
        self.navigation = self.queues.claim(self.session, self.queue_id, self.token)
        self.session.commit()
        if self.navigation is None:
            return None
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
        key = digest([url, method])
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
        row = self.session.get(CollectionWork, work["id"])
        if row.state in {"pending", "deferred"}:
            row.state = "leased"
        self.session.commit()
        self.work_id = row.id if row.state == "leased" else None
        attempt = self.before_send(url, method, kind="browser")
        self.work_id = None
        network_id = event.get("networkId")
        if network_id:
            self.network_attempts[network_id] = attempt
        self.url_attempts[key] = attempt

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
            self.session.commit()

    def persist(self, result, depth=0, asset_type=None):
        key = digest([project_url(result.final_url), result.method])
        self.latest_attempt = self.url_attempts.get(key)
        if self.latest_attempt is None:
            # A cached/unattributed response cannot be invented as a sent request.
            raise ConflictError("collection_response_unattributed")
        attempt = self.session.get(CollectionAttempt, self.latest_attempt)
        if attempt.state == "possibly_sent":
            self.response(result.status_code, result.headers.get("retry-after"))
        asset = super().persist(result, depth, asset_type)
        work = self.session.scalar(
            select(CollectionWork).where(
                CollectionWork.queue_id == self.queue_id, CollectionWork.request_key == key
            )
        )
        if work and work.state == "leased":
            self.queues.complete(self.session, self.queue_id, work.id, self.token)
        self.observed_urls.add(project_url(result.final_url))
        return asset

    def assess_identity(self, url, method, assessment):
        from app.services.collection_transactions import atomic_collection

        with atomic_collection(self.session):
            queue = self.queues.check_lease(self.session, self.queue_id, self.token)
            work = self.session.scalar(
                select(CollectionWork).where(
                    CollectionWork.queue_id == self.queue_id,
                    CollectionWork.request_key == digest([project_url(url), method]),
                )
            )
            if work is None:
                return
            confirmed = assessment == "confirmed"
            if work.reason == "identity_confirmed" and not confirmed:
                return
            self.queues._bump(self.session, queue)
            work.state = "observed" if confirmed else "uncertain"
            work.reason = "identity_confirmed" if confirmed else "identity_uncertain"

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
