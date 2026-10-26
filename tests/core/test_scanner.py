from library_finder.core.scanner import scan_library


def test_scanner_records_inheritance_public_private_methods_and_effects(library_root):
    catalog = scan_library(library_root)

    private = catalog.method("libs.drivers.sample.SampleDriver._click")
    assert private is not None
    assert private.visibility == "internal"
    assert "webdriver" in private.effects
    assert catalog.method("libs.drivers.sample.SampleDriver.click").base_classes == ("BaseDriver",)
    assert "environment" in catalog.method("libs.utils.settings.settings").effects


def test_scanner_reports_syntax_errors_and_retains_valid_files(library_root):
    catalog = scan_library(library_root)

    assert catalog.errors[0].path.endswith("broken.py")
    assert catalog.method("libs.utils.settings.settings") is not None


def test_scanner_excludes_dunder_methods_but_keeps_single_underscore_helpers(library_root):
    sample = library_root / "drivers" / "sample.py"
    sample.write_text(sample.read_text() + "\n    def __init__(self):\n        pass\n")

    catalog = scan_library(library_root)

    assert catalog.method("libs.drivers.sample.SampleDriver.__init__") is None
    assert catalog.method("libs.drivers.sample.SampleDriver._click") is not None
