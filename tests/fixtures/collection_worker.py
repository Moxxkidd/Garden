"""Synthetic subprocess worker exercising the public queue/admission protocol."""

import signal
import sys

import httpx

from app.core.settings import get_settings
from app.db.bootstrap import init_database, session_scope
from app.services.collection_admission import CollectionAdmissionService
from app.services.collection_queue import CollectionQueueService

init_database(get_settings().database_url)
queue_id, phase = int(sys.argv[1]), sys.argv[2]
queues = CollectionQueueService()
admission = CollectionAdmissionService(queues=queues)
with session_scope() as session:
    token = queues.lease(session, queue_id)
    work = queues.claim(session, queue_id, token)
    request = queues.request(session, queue_id, work["id"])
if phase != "claimed":
    with session_scope() as session:
        attempt = admission.reserve(session, queue_id, token, request["url"], work_id=work["id"])
        admission.admit(session, attempt["id"], token)
    response = httpx.get(request["url"], timeout=5, trust_env=False)
    if phase == "acknowledged":
        with session_scope() as session:
            admission.finish(session, attempt["id"], token, status_code=response.status_code)
            queues.complete(session, queue_id, work["id"], token)
print("CHECKPOINT", flush=True)
signal.pause()
