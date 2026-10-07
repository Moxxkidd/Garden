"""Identity contexts, manual login lifecycle and durable recovery references."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scan_contexts", sa.Column("context_key", sa.String(120), nullable=True))
    op.execute("UPDATE scan_contexts SET context_key = kind")
    with op.batch_alter_table("scan_contexts") as batch:
        batch.alter_column("context_key", existing_type=sa.String(120), nullable=False)
        batch.drop_constraint("uq_scan_contexts_run_kind", type_="unique")
        batch.drop_constraint("ck_scan_contexts_kind", type_="check")
        batch.create_check_constraint(
            "ck_scan_contexts_kind", "kind IN ('anonymous','user','admin','identity')"
        )
        batch.create_unique_constraint("uq_scan_contexts_run_key", ["scan_run_id", "context_key"])
        batch.add_column(sa.Column("identity_snapshot", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("health_status", sa.String(40), nullable=True))
        batch.add_column(sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "uq_scan_contexts_legacy_kind",
        "scan_contexts",
        ["scan_run_id", "kind"],
        unique=True,
        sqlite_where=sa.text("kind != 'identity'"),
        postgresql_where=sa.text("kind != 'identity'"),
    )
    for name in ["parent_run_id", "recovery_context_id", "recovery_checkpoint_version"]:
        op.add_column("scan_runs", sa.Column(name, sa.Integer(), nullable=True))
    op.add_column("auth_sessions", sa.Column("identity_metadata", sa.JSON(), nullable=True))
    op.add_column(
        "auth_sessions", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id", sa.Integer(), sa.ForeignKey("credential_profiles.id"), nullable=False
        ),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("auth_sessions.id"), nullable=True),
        sa.Column("owner_token", sa.String(64), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "identity_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("context_id", sa.Integer(), sa.ForeignKey("scan_contexts.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("storage_ref", sa.Text(), nullable=False),
        sa.Column("configuration_digest", sa.String(64), nullable=False),
        sa.Column("pending_count", sa.Integer(), nullable=False),
        sa.Column("uncertain_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("context_id", "version", name="uq_identity_checkpoint_version"),
    )


def downgrade():
    db = op.get_bind()
    for table, condition in [
        ("scan_contexts", "kind = 'identity'"),
        ("scan_runs", "mode = 'identity_collection'"),
        ("login_attempts", "1=1"),
        ("identity_checkpoints", "1=1"),
        ("auth_sessions", "identity_metadata IS NOT NULL OR revoked_at IS NOT NULL"),
    ]:
        if db.execute(sa.text(f"SELECT 1 FROM {table} WHERE {condition} LIMIT 1")).first():
            raise RuntimeError("Cannot downgrade: identity metadata or history would be lost.")
    op.drop_table("identity_checkpoints")
    op.drop_table("login_attempts")
    op.drop_index("uq_scan_contexts_legacy_kind", table_name="scan_contexts")
    with op.batch_alter_table("scan_contexts") as batch:
        batch.drop_constraint("uq_scan_contexts_run_key", type_="unique")
        batch.drop_constraint("ck_scan_contexts_kind", type_="check")
        batch.create_check_constraint(
            "ck_scan_contexts_kind", "kind IN ('anonymous','user','admin')"
        )
        batch.create_unique_constraint("uq_scan_contexts_run_kind", ["scan_run_id", "kind"])
        for name in ["context_key", "identity_snapshot", "health_status", "health_checked_at"]:
            batch.drop_column(name)
    with op.batch_alter_table("scan_runs") as batch:
        for name in ["parent_run_id", "recovery_context_id", "recovery_checkpoint_version"]:
            batch.drop_column(name)
    with op.batch_alter_table("auth_sessions") as batch:
        batch.drop_column("identity_metadata")
        batch.drop_column("revoked_at")
