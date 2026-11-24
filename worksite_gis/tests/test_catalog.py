from pathlib import Path

from worksite_gis.data_pipeline.validate import validate_output


def test_validation_rejects_non_wgs84_geojson():
    path = Path(".test-artifacts") / "bad.geojson"
    path.parent.mkdir(exist_ok=True)
    path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    report = validate_output(path, expected_kind="vector")
    assert report.ok is False
    assert any("EPSG:4326" in error or "no features" in error for error in report.errors)
