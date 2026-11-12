"""Plan submission uses existing single-origin children and fixed quotas."""

import pytest

from app.core.errors import InputValidationError
from app.db.bootstrap import session_scope
from app.models.target import Target
from app.schemas.identity import IdentityPlanItem, IdentityPlanRequest
from app.services.identity_collection import IdentityCollectionService
from app.services.identity_plans import IdentityPlanService
from app.services.identity_preview import IdentityPreviewService
from tests.helpers.project_assets import project_fixture


class HoldingDispatcher:
    def submit(self, *args):
        pass


def test_plan_fixed_allocation_preview_binding_and_idempotent_submission(seeded_records):
    with session_scope() as session:
        target = session.get(Target, seeded_records["target"].id)
        project, entry = project_fixture(session, target)
        service = IdentityPlanService(
            IdentityPreviewService(IdentityCollectionService(dispatcher=HoldingDispatcher()))
        )
        request = IdentityPlanRequest(
            project_id=project.id,
            total_requests=9,
            items=[
                IdentityPlanItem(entry_id=entry.id),
                IdentityPlanItem(
                    entry_id=entry.id, profile_id=seeded_records["credential"].id, max_requests=4
                ),
            ],
        )
        preview = service.preview(session, request)
        assert [i["max_requests"] for i in preview["items"]] == [5, 4]
        result = service.start(session, preview["preview_token"])
        repeated = service.start(session, preview["preview_token"])
        assert repeated["id"] == result["id"]
        assert len({i["run_id"] for i in result["items"]}) == 2
        assert sum(i["max_requests"] for i in result["items"]) == 9
        with pytest.raises(InputValidationError):
            service.preview(session, request.model_copy(update={"total_requests": 3}))
        stale = service.preview(session, request)
        target.base_url = "http://localhost:8222/"
        session.flush()
        with pytest.raises(InputValidationError):
            service.start(session, stale["preview_token"])
