"""Nullable durable telemetry; existing queues remain unmeasured."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "collection_queues", sa.Column("telemetry", sa.JSON(none_as_null=True), nullable=True)
    )

    op.add_column("collection_attempts", sa.Column("is_retry", sa.Boolean(), nullable=True))


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM collection_queues WHERE telemetry IS NOT NULL")
    ):
        raise RuntimeError("Cannot discard collection history metrics during downgrade")
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM collection_attempts WHERE is_retry IS NOT NULL")
    ):
        raise RuntimeError("Cannot discard collection history metrics during downgrade")
    op.drop_column("collection_attempts", "is_retry")
    op.drop_column("collection_queues", "telemetry")
