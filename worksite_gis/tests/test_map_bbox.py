from pathlib import Path


MAP_JS = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
DASHBOARD_CSS = Path("home/static/home/css/dashboard.css").read_text(encoding="utf-8")


def test_geology_requests_use_a_ready_map_and_strict_bbox_validation():
    assert "function geologyBbox()" in MAP_JS
    assert "map._loaded" in MAP_JS
    assert "container.clientWidth > 0" in MAP_JS
    assert "container.clientHeight > 0" in MAP_JS
    assert "southWest.lng < northEast.lng" in MAP_JS
    assert "southWest.lat < northEast.lat" in MAP_JS
    assert MAP_JS.count("geologyBbox()") >= 2


def test_invalid_geology_bbox_is_reported_without_fetching():
    assert "if (!bbox)" in MAP_JS
    assert "地圖尚未完成初始化，暫不載入地質資料" in MAP_JS
    assert "function geologyRequestUrl" in MAP_JS


def test_geology_bbox_rejects_near_zero_and_non_finite_bounds():
    assert "const MIN_BBOX_SPAN = 0.000001" in MAP_JS
    assert "Number.isFinite(southWest.lng)" in MAP_JS
    assert "Number.isFinite(southWest.lat)" in MAP_JS
    assert "Number.isFinite(northEast.lng)" in MAP_JS
    assert "Number.isFinite(northEast.lat)" in MAP_JS
    assert "lngSpan >= MIN_BBOX_SPAN" in MAP_JS
    assert "latSpan >= MIN_BBOX_SPAN" in MAP_JS


def test_geology_bbox_rejects_ranges_over_backend_limit():
    assert "const MAX_BBOX_SPAN = 1.5" in MAP_JS
    assert "lngSpan > MAX_BBOX_SPAN || latSpan > MAX_BBOX_SPAN" in MAP_JS


def test_large_geology_bbox_shows_zoom_in_message_without_fetching():
    assert "地圖範圍過大，請放大地圖後再載入工程地質資料。" in MAP_JS
    assert "geologyBboxReason" in MAP_JS
    assert "if (!url)" in MAP_JS
    assert "geologyBboxBlockedText" in MAP_JS


def test_geology_bbox_requests_remain_guarded_by_bbox_validation():
    assert "const bbox = geologyBbox();" in MAP_JS
    assert "if (!bbox)" in MAP_JS


def test_map_layout_has_a_nonzero_desktop_height_fallback():
    assert ".map-stage {" in DASHBOARD_CSS
    assert "min-height: 420px;" in DASHBOARD_CSS
    assert ".map-stage #map {" in DASHBOARD_CSS


def test_map_layout_has_a_nonzero_mobile_height_fallback():
    assert "@media (max-width: 760px)" in DASHBOARD_CSS
    assert "min-height: 320px;" in DASHBOARD_CSS


def test_geology_move_refresh_is_debounced():
    assert "const GEOLOGY_MOVE_DEBOUNCE_MS = 400" in MAP_JS
    assert "clearTimeout(geologyMoveTimer)" in MAP_JS
    assert "setTimeout(() =>" in MAP_JS


def test_geology_requests_are_deduplicated_and_old_responses_are_isolated():
    assert "function geologyRequestKey" in MAP_JS
    assert "projectSlug" in MAP_JS
    assert "layerName" in MAP_JS
    assert "bbox" in MAP_JS
    assert "limit" in MAP_JS
    assert "const geologyLayerRequests = {}" in MAP_JS
    assert "new AbortController()" in MAP_JS
    assert "request.sequence" in MAP_JS


def test_layer_completion_does_not_refresh_summary_for_every_layer():
    layer_function = MAP_JS[MAP_JS.index("async function refreshGeologyLayer"):MAP_JS.index("function createGeologyControls")]
    assert "refreshGeologySummary()" not in layer_function
    assert "scheduleGeologyRefresh" in MAP_JS


def test_geology_loading_preserves_existing_summary_and_layer_content():
    assert "geology-summary-loading" in MAP_JS
    assert "更新中" in MAP_JS
    assert "data-geology-screening-layer" in MAP_JS
    assert "box.replaceChildren();" not in MAP_JS[MAP_JS.index("async function refreshGeologySummary"):MAP_JS.index("function renderGeologyScreening")]


def test_geology_error_does_not_replace_other_layer_screening():
    assert "querySelectorAll('[data-geology-screening-layer]')" in MAP_JS
    assert "AbortError" in MAP_JS
