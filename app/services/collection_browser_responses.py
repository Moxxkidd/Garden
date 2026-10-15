"""Bounded CDP response capture with exact per-send attribution."""

from base64 import b64decode
from hashlib import sha256

from app.schemas.scan import FetchResult


class CollectionBrowserResponses:
    def __init__(self, cdp, runtime, on_response, on_error):
        self.cdp, self.runtime = cdp, runtime
        self.on_response, self.on_error = on_response, on_error
        self.pending = {}
        self.closed = False
        cdp.send(
            "Network.enable",
            {"maxTotalBufferSize": 1024 * 1024, "maxResourceBufferSize": 256 * 1024},
        )
        self.listeners = {
            "Network.responseReceived": self.received,
            "Network.requestWillBeSent": self.redirected,
            "Network.loadingFinished": self.finished,
            "Network.loadingFailed": self.failed,
        }
        for event, handler in self.listeners.items():
            cdp.on(event, handler)

    def sent(self, event, route, method):
        # Use the actual method passed to Fetch.continueRequest (including HEAD recovery).
        effective = {**event, "request": {**event["request"], "method": method}}
        attempt = self.runtime.sending(effective, route)
        self.pending[event["networkId"]] = {
            "attempt": attempt,
            "url": event["request"]["url"],
            "method": method,
            "route": route,
        }

    def received(self, event):
        if self.closed or event["requestId"] not in self.pending:
            return
        try:
            self.runtime.network_response(event)
            self.pending[event["requestId"]]["response"] = event["response"]
        except Exception as error:
            self.on_error(error)

    def redirected(self, event):
        if "redirectResponse" in event:
            self.received({**event, "response": event["redirectResponse"]})
            self.emit(event["requestId"], finished=False)

    def finished(self, event):
        self.emit(event["requestId"], finished=True)

    def failed(self, event):
        if self.closed:
            return
        try:
            self.runtime.network_failed(event)
        except Exception as error:
            self.on_error(error)
        self.emit(event["requestId"], finished=False)

    def emit(self, request_id, *, finished):
        record = self.pending.pop(request_id, None)
        if self.closed or not record or "response" not in record:
            return
        try:
            response = record["response"]
            headers = {k.lower(): str(v) for k, v in response.get("headers", {}).items()}
            content_type = headers.get("content-type", "")
            is_text = any(t in content_type.lower() for t in ("text/", "json", "javascript", "xml"))
            raw_length = headers.get("content-length", "")
            length = int(raw_length) if raw_length.isdigit() else None
            bounded = (
                length is not None
                and 0 <= length <= 256 * 1024
                and not headers.get("content-encoding")
                and not headers.get("transfer-encoding")
            )
            body, complete = b"", False
            if bounded and is_text and finished:
                from playwright.sync_api import Error as PlaywrightError

                try:
                    payload = self.cdp.send("Network.getResponseBody", {"requestId": request_id})
                    body = (
                        b64decode(payload["body"])
                        if payload["base64Encoded"]
                        else payload["body"].encode("utf-8")
                    )
                    complete = len(body) == length and len(body) <= 256 * 1024
                except PlaywrightError:
                    pass
            if not complete:
                body = b""
            result = FetchResult(
                requested_url=record["url"],
                final_url=response["url"],
                method=record["method"],
                route_url=record["route"],
                status_code=int(response["status"]),
                headers=headers,
                content_type=content_type,
                body_text=body.decode("utf-8", errors="replace"),
                body_size_bytes=len(body) if complete else (length or 0),
                body_is_text=is_text,
                body_truncated=not complete,
                body_sha256=sha256(body).hexdigest() if complete else "",
            )
            result._collection_attempt_id = record["attempt"]
            self.on_response(result, record["route"])
        except Exception as error:
            self.on_error(error)

    def close(self):
        # Detach before final identity proof; context.close() can pump late events.
        for event, handler in self.listeners.items():
            self.cdp.remove_listener(event, handler)
        for request_id in list(self.pending):
            self.emit(request_id, finished=False)
        self.closed = True
