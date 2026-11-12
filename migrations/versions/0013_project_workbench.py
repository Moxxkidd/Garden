"""Optional human review and project saved queries, without changing asset facts."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("review_state", sa.String(20), nullable=False, server_default="unreviewed"),
        sa.Column("review_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_revision", sa.Integer(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
    ):
        op.add_column("project_annotations", column)
    op.create_table(
        "project_saved_views",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("query", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("project_id", "name", name="uq_project_saved_view"),
    )
    op.create_index("ix_project_saved_views_project_id", "project_saved_views", ["project_id"])


def downgrade():
    op.drop_table("project_saved_views")
    for name in ("needs_review", "review_revision", "reviewed_at", "review_reason", "review_state"):
        op.drop_column("project_annotations", name)
