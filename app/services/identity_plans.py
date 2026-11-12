"""Fixed-quota plans compose existing origin-bound identity tasks."""

from copy import deepcopy
from hashlib import sha256

from sqlalchemy import select

from app.core.errors import GardenError, InputValidationError
from app.models.identity_plan import IdentityPlan
from app.models.project import ProjectEntry
from app.models.scan_run import ScanRun
from app.schemas.identity import IdentityCollectionRequest
from app.schemas.scan import ScanOptions
from app.services.identity_flow import identity_flow
from app.services.project_tokens import ProjectTokens
from app.services.projects import ProjectService


class IdentityPlanService:
    def __init__(self, preview):
        self.identities = preview
        self.tokens = ProjectTokens()

    def _children(self, session, request):
        ProjectService().writable(session, request.project_id)
        reserved = sum(i.max_requests or 0 for i in request.items)
        count = sum(i.max_requests is None for i in request.items)
        if reserved > request.total_requests or request.total_requests - reserved < count:
            raise InputValidationError("总预算不足，不能覆盖已指定配额与剩余入口。")
        share, extra = divmod(request.total_requests - reserved, count) if count else (0, 0)
        children, display = [], []
        for item in request.items:
            quota = item.max_requests
            if quota is None:
                quota = min(2000, share + (1 if extra > 0 else 0))
                extra = max(0, extra - 1)
            entry = session.get(ProjectEntry, item.entry_id)
            if not entry or entry.project_id != request.project_id:
                raise InputValidationError("入口不属于当前项目。")
            ProjectService().validate_entry(session, request.project_id, entry.id)
            child = IdentityCollectionRequest(
                project_entry_id=entry.id,
                target_id=entry.target_id,
                url=entry.entry_url,
                profile_ids=[item.profile_id] if item.profile_id else [],
                include_anonymous=item.profile_id is None,
                interaction_mode=item.interaction_mode,
                options=ScanOptions(
                    max_browser_requests=quota,
                ),
            )
            binding, profiles = self.identities.binding(session, child)
            children.append((child, binding))
            display.append(
                dict(
                    entry_id=entry.id,
                    entry_name=entry.name,
                    profile_id=item.profile_id,
                    profiles=profiles,
                    max_requests=quota,
                    interaction_mode=item.interaction_mode,
                    identity_state=identity_flow(session, item.profile_id)["label"]
                    if item.profile_id
                    else "匿名",
                )
            )
        return children, display

    def preview(self, session, request):
        children, items = self._children(session, request)
        token = self.tokens.put((request, children))
        return dict(
            project_id=request.project_id,
            preview_token=token,
            total_requests=request.total_requests,
            allocated_requests=sum(i["max_requests"] for i in items),
            items=items,
        )

    def start(self, session, token):
        with self.tokens.lock:
            key = sha256(token.encode()).hexdigest()
            existing = session.scalar(select(IdentityPlan).where(IdentityPlan.token_digest == key))
            if existing:
                return self.view(session, existing.id)
            request, children = self.tokens.get(token)
            for child, binding in children:
                if self.identities.binding(session, child)[0] != binding:
                    raise InputValidationError("入口、身份或会话已变化，请重新预览整个计划。")
            ProjectService().writable(session, request.project_id)
            config = dict(
                total_requests=request.total_requests,
                items=[
                    dict(
                        entry_id=child.project_entry_id,
                        profile_id=child.profile_ids[0] if child.profile_ids else None,
                        max_requests=child.options.max_browser_requests,
                        interaction_mode=child.interaction_mode,
                        run_id=None,
                        error_code=None,
                    )
                    for child, _ in children
                ],
            )
            plan = IdentityPlan(
                project_id=request.project_id, token_digest=key, configuration=config
            )
            session.add(plan)
            session.commit()
            for index, (child, _) in enumerate(children):
                config = deepcopy(plan.configuration)
                try:
                    run_id = self.identities.collection.start(session, child)
                    config["items"][index]["run_id"] = run_id
                except GardenError:
                    session.rollback()
                    config["items"][index]["error_code"] = "child_start_failed"
                plan.configuration = config
                session.commit()
            return self.view(session, plan.id)

    def view(self, session, plan_id):
        plan = session.get(IdentityPlan, plan_id)
        if not plan:
            raise InputValidationError("采集计划不存在。")
        items = deepcopy(plan.configuration["items"])
        for item in items:
            run = session.get(ScanRun, item["run_id"]) if item["run_id"] else None
            item["status"] = run.status if run else "not_started"
        return dict(
            id=plan.id,
            project_id=plan.project_id,
            total_requests=plan.configuration["total_requests"],
            items=items,
        )
