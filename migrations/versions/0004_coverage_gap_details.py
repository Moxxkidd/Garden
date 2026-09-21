"""Persist nullable structured budget gap details without guessing legacy counts."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scan_failures", sa.Column("coverage_details", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("scan_failures") as batch:
        batch.drop_column("coverage_details")
