from app.services.inventory_annotations import InventoryAnnotationService


def test_inventory_annotation_rules_cover_paths_and_parameters() -> None:
    service = InventoryAnnotationService()

    page_annotations = service.page_annotations("http://localhost:8000/admin/config")
    endpoint_annotations = service.endpoint_annotations("GET", "/demo/auth/app/api/actuator/health")
    parameter_annotations = service.parameter_annotations("query", "userId")

    assert {item.marker for item in page_annotations} == {"admin", "config"}
    assert {item.marker for item in endpoint_annotations} == {"actuator"}
    assert {item.marker for item in parameter_annotations} == {"userid"}
    assert service.parameter_is_sensitive("sessionToken") is True
