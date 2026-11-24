import json
from pathlib import Path

import geopandas as gpd
from shapely import wkt
from shapely.geometry import mapping

from worksite_gis.data_pipeline.simplify import simplify_geojson


def test_simplify_geojson_preserves_features_and_reduces_display_size():
    root = Path(".test-artifacts") / "simplify"
    root.mkdir(parents=True, exist_ok=True)
    source = root / "detailed.geojson"
    output = root / "display.geojson"
    bottom = [[120 + index / 10000, 23] for index in range(1001)]
    right = [[120.1, 23 + index / 10000] for index in range(1, 1001)]
    top = [[120.1 - index / 10000, 23.1] for index in range(1, 1001)]
    left = [[120, 23.1 - index / 10000] for index in range(1, 1001)]
    ring = bottom + right + top + left
    source.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {"level": "high"},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        }],
    }), encoding="utf-8")

    result = simplify_geojson(source, output, tolerance=0.0005)
    simplified = gpd.read_file(output)

    assert result.feature_count == 1
    assert result.crs == "EPSG:4326"
    assert len(simplified) == 1
    assert simplified.geometry.iloc[0].is_valid
    assert output.stat().st_size < source.stat().st_size / 4


def test_simplify_geojson_reduces_tolerance_when_multipolygon_becomes_invalid():
    root = Path(".test-artifacts") / "simplify-fallback"
    root.mkdir(parents=True, exist_ok=True)
    source = root / "nested-shell-regression.geojson"
    output = root / "nested-shell-display.geojson"
    geometry = wkt.loads(
        "MULTIPOLYGON (((121.513495 25.108529,121.513892 25.108708,121.514189 25.108437,121.51419 25.108707,121.515282 25.109065,121.515378 25.108252,121.515675 25.108251,121.515574 25.10771,121.51597 25.107528,121.515276 25.10753,121.515374 25.107259,121.516066 25.106805,121.516068 25.107347,121.516464 25.107255,121.516165 25.106714,121.51745 25.105807,121.517448 25.105356,121.516261 25.106082,121.515965 25.106354,121.516065 25.106534,121.515966 25.106535,121.515966 25.106625,121.515569 25.106446,121.515571 25.106897,121.514976 25.106899,121.514977 25.10717,121.513983 25.106632,121.514482 25.107352,121.513495 25.108529)),((121.515866 25.106445,121.515966 25.106535,121.515965 25.106444,121.515866 25.106445)))"
    )
    assert geometry.is_valid
    assert not geometry.simplify(0.0005, preserve_topology=True).is_valid
    source.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "properties": {}, "geometry": mapping(geometry)}],
    }), encoding="utf-8")

    simplify_geojson(source, output, tolerance=0.0005)

    assert gpd.read_file(output).geometry.iloc[0].is_valid
