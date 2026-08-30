"""建立统一验证运行模型。

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _legacy_asset_identity(method: str | None, url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.rstrip("/") or "/"
    parameter_names = sorted(
        {name for name, _value in parse_qsl(parsed.query, keep_blank_values=True)}
    )
    query_suffix = f"?{'&'.join(parameter_names)}" if parameter_names else ""
    return f"{(method or 'GET').upper()}:{path}{query_suffix}"


def _replace_scan_asset_unique_constraint(*, restoring_legacy: bool = False) -> None:
    bind = op.get_bind()
    constraints = sa.inspect(bind).get_unique_constraints("scan_assets")
    expected_columns = (
        {"scan_run_id", "context_id", "asset_type", "url"}
        if restoring_legacy
        else {"scan_run_id", "asset_type", "url"}
    )
    old_constraint = next(
        item for item in constraints if set(item.get("column_names") or ()) == expected_columns
    )
    old_name = old_constraint.get("name")
    naming_convention = None
    if old_name is None:
        naming_convention = {"uq": "uq_%(table_name)s_%(column_0_N_name)s"}
        old_name = "uq_scan_assets_" + "_".join(old_constraint["column_names"])

    with op.batch_alter_table(
        "scan_assets",
        naming_convention=naming_convention,
    ) as batch_op:
        batch_op.drop_constraint(old_name, type_="unique")
        if restoring_legacy:
            batch_op.create_unique_constraint(
                "uq_scan_assets_scan_run_id_asset_type_url",
                ["scan_run_id", "asset_type", "url"],
            )
        else:
            batch_op.create_unique_constraint(
                "uq_scan_assets_run_context_type_url",
                ["scan_run_id", "context_id", "asset_type", "url"],
            )


def _backfill_quick_contexts_and_asset_identity() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    scan_runs = sa.Table("scan_runs", metadata, autoload_with=bind)
    scan_contexts = sa.Table("scan_contexts", metadata, autoload_with=bind)
    scan_assets = sa.Table("scan_assets", metadata, autoload_with=bind)

    now = datetime.now(timezone.utc)
    runs = bind.execute(sa.select(scan_runs)).mappings().all()
    for run in runs:
        context_status = str(run["status"])
        collection_status = context_status if context_status != "queued" else "pending"
        if context_status in {"completed", "completed_with_warnings"}:
            completeness = "complete"
        elif context_status == "failed":
            completeness = "incomplete"
        else:
            completeness = "pending"
        bind.execute(
            scan_runs.update().where(scan_runs.c.id == run["id"]).values(completeness=completeness)
        )
        bind.execute(
            scan_contexts.insert().values(
                scan_run_id=run["id"],
                kind="anonymous",
                status=context_status,
                login_status="skipped",
                session_validation_status="skipped",
                collection_status=collection_status,
                completeness=completeness,
                asset_count=0,
                request_count=0,
                failure_count=0,
                created_at=run.get("created_at") or now,
                updated_at=run.get("updated_at") or now,
            )
        )

    context_by_run = dict(
        bind.execute(
            sa.select(scan_contexts.c.scan_run_id, scan_contexts.c.id).where(
                scan_contexts.c.kind == "anonymous"
            )
        ).all()
    )
    assets = bind.execute(
        sa.select(
            scan_assets.c.id,
            scan_assets.c.scan_run_id,
            scan_assets.c.method,
            scan_assets.c.url,
        )
    ).mappings()
    for asset in assets:
        bind.execute(
            scan_assets.update()
            .where(scan_assets.c.id == asset["id"])
            .values(
                context_id=context_by_run.get(asset["scan_run_id"]),
                identity_key=_legacy_asset_identity(asset["method"], asset["url"]),
            )
        )

    asset_counts = dict(
        bind.execute(
            sa.select(scan_assets.c.context_id, sa.func.count(scan_assets.c.id))
            .where(scan_assets.c.context_id.is_not(None))
            .group_by(scan_assets.c.context_id)
        ).all()
    )
    for context_id, asset_count in asset_counts.items():
        bind.execute(
            scan_contexts.update()
            .where(scan_contexts.c.id == context_id)
            .values(asset_count=asset_count)
        )


def upgrade() -> None:
    with op.batch_alter_table("scan_runs") as batch_op:
        batch_op.add_column(
            sa.Column("mode", sa.String(length=40), server_default="quick", nullable=False)
        )
        batch_op.add_column(sa.Column("target_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("source_run_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "active_checks_enabled",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("authorization_confirmed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("authorization_confirmed_by", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "completeness", sa.String(length=64), server_default="pending", nullable=False
            )
        )
        batch_op.create_foreign_key(
            "fk_scan_runs_target_id_targets", "targets", ["target_id"], ["id"]
        )
        batch_op.create_foreign_key(
            "fk_scan_runs_source_run_id_scan_runs", "scan_runs", ["source_run_id"], ["id"]
        )
        batch_op.create_index("ix_scan_runs_mode", ["mode"], unique=False)
        batch_op.create_index("ix_scan_runs_target_id", ["target_id"], unique=False)
        batch_op.create_index("ix_scan_runs_source_run_id", ["source_run_id"], unique=False)
        batch_op.create_index("ix_scan_runs_completeness", ["completeness"], unique=False)

    op.create_table(
        "scan_contexts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("credential_profile_id", sa.Integer(), nullable=True),
        sa.Column("temporary_secret_ref", sa.String(length=1000), nullable=True),
        sa.Column("auth_session_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=40), server_default="pending", nullable=False),
        sa.Column("login_status", sa.String(length=40), server_default="pending", nullable=False),
        sa.Column(
            "session_validation_status",
            sa.String(length=40),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "collection_status", sa.String(length=40), server_default="pending", nullable=False
        ),
        sa.Column("completeness", sa.String(length=64), server_default="pending", nullable=False),
        sa.Column("asset_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
        sa.ForeignKeyConstraint(["credential_profile_id"], ["credential_profiles.id"]),
        sa.ForeignKeyConstraint(["auth_session_id"], ["auth_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('anonymous', 'user', 'admin')",
            name="ck_scan_contexts_kind",
        ),
        sa.UniqueConstraint("scan_run_id", "kind", name="uq_scan_contexts_run_kind"),
        sa.UniqueConstraint("scan_run_id", "id", name="uq_scan_contexts_run_id_id"),
        sa.UniqueConstraint(
            "temporary_secret_ref",
            name="uq_scan_contexts_temporary_secret_ref",
        ),
    )
    op.create_index("ix_scan_contexts_scan_run_id", "scan_contexts", ["scan_run_id"])
    op.create_index("ix_scan_contexts_kind", "scan_contexts", ["kind"])
    op.create_index(
        "ix_scan_contexts_credential_profile_id", "scan_contexts", ["credential_profile_id"]
    )
    op.create_index("ix_scan_contexts_auth_session_id", "scan_contexts", ["auth_session_id"])
    op.create_index("ix_scan_contexts_status", "scan_contexts", ["status"])
    op.create_index("ix_scan_contexts_login_status", "scan_contexts", ["login_status"])
    op.create_index(
        "ix_scan_contexts_session_validation_status",
        "scan_contexts",
        ["session_validation_status"],
    )
    op.create_index("ix_scan_contexts_collection_status", "scan_contexts", ["collection_status"])
    op.create_index("ix_scan_contexts_completeness", "scan_contexts", ["completeness"])

    with op.batch_alter_table("scan_assets") as batch_op:
        batch_op.add_column(sa.Column("context_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("identity_key", sa.String(length=1000), nullable=True))
        batch_op.create_foreign_key(
            "fk_scan_assets_run_context",
            "scan_contexts",
            ["scan_run_id", "context_id"],
            ["scan_run_id", "id"],
        )
        batch_op.create_unique_constraint("uq_scan_assets_run_id_id", ["scan_run_id", "id"])
        batch_op.create_index("ix_scan_assets_context_id", ["context_id"], unique=False)
        batch_op.create_index("ix_scan_assets_identity_key", ["identity_key"], unique=False)
    _replace_scan_asset_unique_constraint()
    _backfill_quick_contexts_and_asset_identity()

    op.create_table(
        "scan_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("source_context_id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=True),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("normalized_redacted_url", sa.String(length=1000), nullable=False),
        sa.Column("header_names", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=128), nullable=False),
        sa.Column("replay_allowed", sa.Boolean(), nullable=False),
        sa.Column("replay_status", sa.String(length=40), nullable=False),
        sa.Column("replay_reason", sa.Text(), nullable=True),
        sa.Column("protected_storage_ref", sa.String(length=1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
        sa.ForeignKeyConstraint(
            ["scan_run_id", "source_context_id"],
            ["scan_contexts.scan_run_id", "scan_contexts.id"],
            name="fk_scan_requests_run_source_context",
        ),
        sa.ForeignKeyConstraint(
            ["scan_run_id", "asset_id"],
            ["scan_assets.scan_run_id", "scan_assets.id"],
            name="fk_scan_requests_run_asset",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scan_run_id",
            "id",
            "source_context_id",
            name="uq_scan_requests_run_id_source_context",
        ),
    )
    for column in (
        "scan_run_id",
        "source_context_id",
        "asset_id",
        "method",
        "fingerprint",
        "replay_status",
    ):
        op.create_index(f"ix_scan_requests_{column}", "scan_requests", [column])

    op.create_table(
        "coverage_differences",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("identity_key", sa.String(length=1000), nullable=False),
        sa.Column("classification", sa.String(length=64), nullable=False),
        sa.Column("anonymous_state", sa.String(length=32), nullable=False),
        sa.Column("user_state", sa.String(length=32), nullable=False),
        sa.Column("admin_state", sa.String(length=32), nullable=False),
        sa.Column("anonymous_present", sa.Boolean(), nullable=True),
        sa.Column("user_present", sa.Boolean(), nullable=True),
        sa.Column("admin_present", sa.Boolean(), nullable=True),
        sa.Column("context_summaries", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column("diagnostic", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scan_run_id", "identity_key"),
    )
    op.create_index("ix_coverage_differences_scan_run_id", "coverage_differences", ["scan_run_id"])
    op.create_index(
        "ix_coverage_differences_identity_key", "coverage_differences", ["identity_key"]
    )
    op.create_index(
        "ix_coverage_differences_classification", "coverage_differences", ["classification"]
    )
    op.create_index("ix_coverage_differences_confidence", "coverage_differences", ["confidence"])

    op.create_table(
        "replay_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scan_run_id", sa.Integer(), nullable=False),
        sa.Column("source_request_id", sa.Integer(), nullable=False),
        sa.Column("source_context_id", sa.Integer(), nullable=False),
        sa.Column("target_context_id", sa.Integer(), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("policy_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redirects", sa.JSON(), nullable=False),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_final_url_redacted", sa.String(length=1000), nullable=True),
        sa.Column("response_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("response_summary_redacted", sa.JSON(), nullable=False),
        sa.Column("verdict", sa.String(length=40), nullable=True),
        sa.Column("verdict_reason", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["scan_run_id"], ["scan_runs.id"]),
        sa.ForeignKeyConstraint(
            ["scan_run_id", "source_request_id", "source_context_id"],
            [
                "scan_requests.scan_run_id",
                "scan_requests.id",
                "scan_requests.source_context_id",
            ],
            name="fk_replay_executions_run_source_request_context",
        ),
        sa.ForeignKeyConstraint(
            ["scan_run_id", "source_context_id"],
            ["scan_contexts.scan_run_id", "scan_contexts.id"],
            name="fk_replay_executions_run_source_context",
        ),
        sa.ForeignKeyConstraint(
            ["scan_run_id", "target_context_id"],
            ["scan_contexts.scan_run_id", "scan_contexts.id"],
            name="fk_replay_executions_run_target_context",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scan_run_id", "source_request_id", "target_context_id", "policy_hash"),
    )
    for column in (
        "scan_run_id",
        "source_request_id",
        "source_context_id",
        "target_context_id",
        "status",
        "verdict",
    ):
        op.create_index(f"ix_replay_executions_{column}", "replay_executions", [column])


def downgrade() -> None:
    op.drop_table("replay_executions")
    op.drop_table("coverage_differences")
    op.drop_table("scan_requests")

    _replace_scan_asset_unique_constraint(restoring_legacy=True)
    with op.batch_alter_table("scan_assets") as batch_op:
        batch_op.drop_index("ix_scan_assets_identity_key")
        batch_op.drop_index("ix_scan_assets_context_id")
        batch_op.drop_constraint("fk_scan_assets_run_context", type_="foreignkey")
        batch_op.drop_constraint("uq_scan_assets_run_id_id", type_="unique")
        batch_op.drop_column("identity_key")
        batch_op.drop_column("context_id")

    op.drop_table("scan_contexts")
    with op.batch_alter_table("scan_runs") as batch_op:
        batch_op.drop_index("ix_scan_runs_completeness")
        batch_op.drop_index("ix_scan_runs_source_run_id")
        batch_op.drop_index("ix_scan_runs_target_id")
        batch_op.drop_index("ix_scan_runs_mode")
        batch_op.drop_constraint("fk_scan_runs_source_run_id_scan_runs", type_="foreignkey")
        batch_op.drop_constraint("fk_scan_runs_target_id_targets", type_="foreignkey")
        batch_op.drop_column("completeness")
        batch_op.drop_column("authorization_confirmed_by")
        batch_op.drop_column("authorization_confirmed_at")
        batch_op.drop_column("active_checks_enabled")
        batch_op.drop_column("source_run_id")
        batch_op.drop_column("target_id")
        batch_op.drop_column("mode")
