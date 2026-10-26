from core.models import Catalog, FileRecord, MethodRecord


def test_catalog_round_trip_preserves_private_method_and_effects():
    record = MethodRecord(
        qualified_name="libs.drivers.SampleDriver._click",
        module="libs.drivers",
        file="drivers/sample.py",
        line=12,
        visibility="internal",
        owner_class="SampleDriver",
        base_classes=("BaseDriver",),
        parameters=("self", "locator"),
        return_annotation="None",
        decorators=(),
        docstring="Click a locator.",
        imports=("selenium",),
        keywords=("click", "locator"),
        effects=("webdriver",),
    )
    catalog = Catalog(
        version=Catalog.CURRENT_VERSION,
        source_root="libs",
        files=(FileRecord(path="drivers/sample.py", sha256="abc"),),
        methods=(record,),
        errors=(),
        created_at="2026-10-22T00:00:00+00:00",
    )

    assert Catalog.from_dict(catalog.to_dict()) == catalog
