from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAP_TEMPLATE = (ROOT / "home/templates/home/map.html").read_text(encoding="utf-8")
MAP_JS = (ROOT / "home/static/home/js/map.js").read_text(encoding="utf-8")
CSS = (ROOT / "home/static/home/css/dashboard.css").read_text(encoding="utf-8")


def test_empty_worksite_state_explains_next_step():
    assert "目前尚未建立工區" in MAP_TEMPLATE
    assert "請先建立一個工區，才能進行工程地質初步檢核。" in MAP_TEMPLATE
    assert "建立第一個工區" in MAP_TEMPLATE


def test_worksite_flow_keeps_beginner_actions_visible():
    assert 'id="create-first-worksite"' in MAP_TEMPLATE
    assert 'id="edit-worksite"' in MAP_TEMPLATE
    assert 'id="save-plan-draft"' in MAP_TEMPLATE
    assert 'data-draw-mode="point"' in MAP_TEMPLATE
    assert 'data-draw-mode="polyline"' in MAP_TEMPLATE
    assert 'data-draw-mode="polygon"' in MAP_TEMPLATE
    assert 'data-draw-mode="rectangle"' in MAP_TEMPLATE
    assert 'id="clear-plan-draft"' in MAP_TEMPLATE


def test_location_search_cannot_fall_back_to_native_form_navigation():
    assert 'id="location-search-form"' in MAP_TEMPLATE
    assert 'method="post"' in MAP_TEMPLATE
    assert 'name="query"' in MAP_TEMPLATE
    assert 'onsubmit="return false;"' not in MAP_TEMPLATE
    assert "event.preventDefault()" in MAP_JS
    assert "map.js' %}?v=20260825-worksite-search1-phase3" in MAP_TEMPLATE


def test_geology_layers_and_technical_details_are_collapsed_by_default():
    assert 'id="geology-layer-details"' in MAP_TEMPLATE
    assert '<details' in MAP_TEMPLATE
    assert 'summary>查看地圖圖層</summary>' in MAP_TEMPLATE
    assert 'summary>查看詳細資料</summary>' in MAP_TEMPLATE
    assert 'id="geology-technical-details"' in MAP_TEMPLATE


def test_simplified_summary_uses_plain_language_and_keeps_limitations():
    ui_source = MAP_TEMPLATE + MAP_JS
    assert "工區範圍" in ui_source
    assert "工程地質鑽孔" in ui_source
    assert "目前視窗探索" in ui_source
    assert "資料發布日期" in ui_source and "系統取得時間" in ui_source
    assert "此結果為官方資料的空間提示，不代表正式地質鑑定或施工安全判定。" in MAP_TEMPLATE
    assert "no_worksite_geometry" not in MAP_TEMPLATE


def test_ui_can_switch_between_empty_and_ready_worksite_states():
    assert "worksite-empty-state" in MAP_JS
    assert "worksite-ready-state" in MAP_JS
    assert "setWorksiteUiState" in MAP_JS
    assert "create-first-worksite" in MAP_JS
    assert "edit-worksite" in MAP_JS


def test_existing_geology_safety_contracts_remain_in_frontend():
    assert "MIN_BBOX_SPAN = 0.000001" in MAP_JS
    assert "MAX_BBOX_SPAN = 1.5" in MAP_JS
    for layer in ("drill", "liquefaction", "dip_slope", "rock_fall", "debris_flow_track"):
        assert f"geologyLayerItems =" in MAP_JS
        assert layer in MAP_JS


def test_map_height_and_compact_worksite_css_are_retained():
    assert "min-height: 420px;" in CSS
    assert "min-height: 320px;" in CSS
    assert ".worksite-empty-state" in CSS
    assert ".geology-layer-details" in CSS


def test_no_new_models_or_migrations_for_ui_simplification():
    assert "models.py" not in MAP_JS
    assert "migrate" not in MAP_JS.lower()


def test_main_map_save_does_not_send_workflow_status():
    save = MAP_JS.split("$('save-plan-draft').addEventListener", 1)[1].splitlines()[0]
    assert "const payload = {" in save
    assert "status:" not in save
    assert "selectedPlan ? 'PATCH' : 'POST'" in save
