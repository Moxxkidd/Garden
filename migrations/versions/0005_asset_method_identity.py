"""Keep distinct HTTP methods as separate stored asset observations."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("inventory_endpoints") as batch:
        batch.drop_constraint("uq_inventory_endpoints_run_method_path", type_="unique")
        batch.create_unique_constraint(
            "uq_inventory_endpoints_run_method_url", ["inventory_run_id", "method", "url"]
        )
    with op.batch_alter_table("scan_assets") as batch:
        batch.drop_constraint("uq_scan_assets_run_context_type_url", type_="unique")
        batch.create_unique_constraint(
            "uq_scan_assets_run_context_type_url_method",
            ["scan_run_id", "context_id", "asset_type", "url", "method"],
        )


def downgrade() -> None:
    # The old constraint cannot represent GET and POST at the same URL. Refuse
    # rather than dropping observations or breaking their request/evidence links.
    duplicates = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT 1 FROM scan_assets WHERE context_id IS NOT NULL
        GROUP BY scan_run_id, context_id, asset_type, url HAVING count(*) > 1 LIMIT 1
    """)
        )
        .first()
    )
    if duplicates:
        raise RuntimeError(
            "Cannot downgrade: distinct asset methods cannot fit the old unique constraint."
        )
    inventory_duplicates = (
        op.get_bind()
        .execute(
            sa.text("""
        SELECT 1 FROM inventory_endpoints
        GROUP BY inventory_run_id, method, path HAVING count(*) > 1 LIMIT 1
    """)
        )
        .first()
    )
    if inventory_duplicates:
        raise RuntimeError(
            "Cannot downgrade: distinct inventory origins cannot fit the old unique constraint."
        )
    with op.batch_alter_table("inventory_endpoints") as batch:
        batch.drop_constraint("uq_inventory_endpoints_run_method_url", type_="unique")
        batch.create_unique_constraint(
            "uq_inventory_endpoints_run_method_path", ["inventory_run_id", "method", "path"]
        )
    with op.batch_alter_table("scan_assets") as batch:
        batch.drop_constraint("uq_scan_assets_run_context_type_url_method", type_="unique")
        batch.create_unique_constraint(
            "uq_scan_assets_run_context_type_url",
            ["scan_run_id", "context_id", "asset_type", "url"],
        )
