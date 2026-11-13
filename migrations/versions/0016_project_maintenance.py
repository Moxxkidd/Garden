"""Project freshness and explicitly enabled local rechecks."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "projects", sa.Column("freshness_days", sa.Integer(), nullable=False, server_default="7")
    )

    op.create_table(
        "project_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("entry_id", sa.Integer(), sa.ForeignKey("project_entries.id"), nullable=False),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("credential_profiles.id")),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("binding", sa.String(64), nullable=False),
        sa.Column("interval_hours", sa.Integer(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True)),
        sa.Column("last_result", sa.String(60)),
        sa.Column("last_run_id", sa.Integer(), sa.ForeignKey("scan_runs.id")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_project_schedules_project_id", "project_schedules", ["project_id"])
    op.create_index("ix_project_schedules_next_at", "project_schedules", ["next_at"])


def downgrade():
    op.drop_table("project_schedules")
    op.drop_column("projects", "freshness_days")
