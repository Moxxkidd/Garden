"""Nullable passive response traits and separately stored unrequested candidates."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scan_runs", sa.Column("asset_metadata", sa.JSON(), nullable=True))
    for table in ["inventory_pages", "inventory_endpoints"]:
        op.add_column(table, sa.Column("response_traits", sa.JSON(), nullable=True))


def downgrade():
    for table, column in [
        ("inventory_endpoints", "response_traits"),
        ("inventory_pages", "response_traits"),
        ("scan_runs", "asset_metadata"),
    ]:
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column)
