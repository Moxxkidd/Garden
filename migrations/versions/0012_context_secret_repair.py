"""Repair the known omitted temporary secret column and its uniqueness guarantee."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "temporary_secret_ref" not in {c["name"] for c in inspector.get_columns("scan_contexts")}:
        op.add_column(
            "scan_contexts", sa.Column("temporary_secret_ref", sa.String(255), nullable=True)
        )
    unique = inspector.get_unique_constraints("scan_contexts") + [
        i for i in inspector.get_indexes("scan_contexts") if i["unique"]
    ]
    if not any(i["column_names"] == ["temporary_secret_ref"] for i in unique):
        op.create_index(
            "uq_scan_contexts_temporary_secret_ref",
            "scan_contexts",
            ["temporary_secret_ref"],
            unique=True,
        )


def downgrade():
    # This restores the historical 0002 contract; downgrading must not recreate the omission.
    pass
