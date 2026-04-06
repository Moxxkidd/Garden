import json
from pathlib import Path

from app.db.bootstrap import session_scope
from app.services.inventory import InventoryBuildService


def test_inventory_exports_generate_json_and_csv(seeded_inventory) -> None:
    service = InventoryBuildService()
    inventory_run_id = seeded_inventory["inventory_run_id"]

    with session_scope() as session:
        json_export = service.export_json(session, inventory_run_id)
        csv_export = service.export_csv(session, inventory_run_id)

    json_path = Path(json_export.output_path)
    csv_path = Path(csv_export.output_path)
    assert json_path.exists() is True
    assert csv_path.is_dir() is True
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["inventory_run"]["id"] == inventory_run_id
    assert (csv_path / "inventory_run.csv").exists() is True
    assert (csv_path / "inventory_pages.csv").exists() is True
    assert (csv_path / "inventory_endpoints.csv").exists() is True
    assert (csv_path / "inventory_parameters.csv").exists() is True
    assert (csv_path / "inventory_annotations.csv").exists() is True
