"""Nullable discovery provenance and protected seed input reference."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scan_runs", sa.Column("discovery_input_ref", sa.Text(), nullable=True))
    for table in ["inventory_pages", "inventory_endpoints"]:
        op.add_column(table, sa.Column("discovery_metadata", sa.JSON(), nullable=True))


def downgrade():
    for table, column in [
        ("inventory_pages", "discovery_metadata"),
        ("inventory_endpoints", "discovery_metadata"),
        ("scan_runs", "discovery_input_ref"),
    ]:
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column)
