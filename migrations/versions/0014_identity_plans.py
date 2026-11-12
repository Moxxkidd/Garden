"""Explicit manual provenance and reference-only multi-entry collection plans."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "scan_requests",
        sa.Column("capture_source", sa.String(20), nullable=True),
    )
    op.create_table(
        "identity_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_identity_plans_project_id", "identity_plans", ["project_id"])


def downgrade():
    op.drop_table("identity_plans")
    op.drop_column("scan_requests", "capture_source")
