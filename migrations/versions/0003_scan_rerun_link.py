"""Link a new scan to the run whose configuration was reused."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scan_runs") as batch:
        batch.add_column(sa.Column("rerun_of_run_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_scan_runs_rerun_of", "scan_runs", ["rerun_of_run_id"], ["id"])
        batch.create_index("ix_scan_runs_rerun_of_run_id", ["rerun_of_run_id"])


def downgrade() -> None:
    with op.batch_alter_table("scan_runs") as batch:
        batch.drop_index("ix_scan_runs_rerun_of_run_id")
        batch.drop_constraint("fk_scan_runs_rerun_of", type_="foreignkey")
        batch.drop_column("rerun_of_run_id")
