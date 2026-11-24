from pathlib import Path
from html.parser import HTMLParser
import re


ROOT = Path(__file__).resolve().parents[2]
MAP_TEMPLATE = (ROOT / "home/templates/home/map.html").read_text(encoding="utf-8")
MAP_JS = (ROOT / "home/static/home/js/map.js").read_text(encoding="utf-8")
CSS = (ROOT / "home/static/home/css/dashboard.css").read_text(encoding="utf-8")


class _ElementIndex(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        element = {"tag": tag, "attrs": dict(attrs), "parent": self.stack[-1] if self.stack else None}
        self.elements.append(element)
        if tag not in {"input", "img", "link", "meta", "br", "hr"}:
            self.stack.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break


def _html_elements():
    parser = _ElementIndex()
    parser.feed(MAP_TEMPLATE)
    return parser.elements


def _by_id(element_id):
    return next((item for item in _html_elements() if item["attrs"].get("id") == element_id), None)


def test_map_ui_has_null_safe_event_binding_and_independent_controls():
    assert "function on(id, event, handler)" in MAP_JS
    assert "on('settings-button', 'click'" in MAP_JS
    assert "on('drawer-toggle', 'click'" in MAP_JS
    assert "on('location-search-form', 'submit'" in MAP_JS


def test_layer_details_updates_accessible_expanded_state():
    assert 'id="geology-layer-details"' in MAP_TEMPLATE
    assert "aria-expanded=\"false\"" in MAP_TEMPLATE
    assert "geologyLayerDetails.addEventListener('toggle'" in MAP_JS


def test_settings_controls_are_bound_without_aborting_other_initialization():
    assert 'id="settings-button"' in MAP_TEMPLATE
    assert 'aria-controls="settings-modal"' in MAP_TEMPLATE
    assert "on('settings-close', 'click'" in MAP_JS
    assert "onSelector('[data-open-settings]', 'click'" in MAP_JS


def test_phase_2y_required_ui_ids_and_relationships_exist_in_actual_html():
    required = (
        "map", "drawer-toggle", "layer-drawer", "geology-layer-details", "layer-list",
        "settings-button", "settings-modal", "worksite-toggle", "worksite-flow",
        "location-search-form", "location-query", "location-candidates", "cctv-layer",
    )
    assert all(_by_id(element_id) for element_id in required)

    drawer_toggle = _by_id("drawer-toggle")
    settings_button = _by_id("settings-button")
    worksite_toggle = _by_id("worksite-toggle")
    assert drawer_toggle["attrs"].get("aria-controls") == "layer-drawer"
    assert settings_button["attrs"].get("aria-controls") == "settings-modal"
    assert worksite_toggle["attrs"].get("aria-controls") == "worksite-flow"
    assert "hidden" in _by_id("worksite-flow")["attrs"]
    assert "hidden" in _by_id("settings-modal")["attrs"]

    assert any("data-open-settings" in item["attrs"] for item in _html_elements())


def test_language_initialization_targets_exist_in_overlay_drawer_section():
    drawer = re.search(
        r'<aside[^>]*id="layer-drawer"[^>]*>(?P<body>.*?)</aside>',
        MAP_TEMPLATE,
        re.DOTALL,
    )
    assert drawer is not None
    sections = re.findall(r"<section>(.*?)</section>", drawer.group("body"), re.DOTALL)
    assert len(sections) >= 2

    overlay_section = sections[1]
    assert re.search(r'class="[^"]*\beyebrow\b[^"]*"', overlay_section)
    assert re.search(r"<h2(?:\s[^>]*)?>", overlay_section)


def test_phase_2y_location_search_input_and_results_are_inside_the_form():
    form = _by_id("location-search-form")
    query = _by_id("location-query")
    candidates = _by_id("location-candidates")
    assert form["tag"] == "form"
    assert query["parent"]["attrs"].get("id") == "location-search-form"
    assert candidates["parent"] is not None
    assert "on('location-search-form', 'submit'" in MAP_JS
    assert "method: 'POST'" in MAP_JS
    assert "/location-search/" in MAP_JS

    method = form["attrs"].get("method")
    action = form["attrs"].get("action")
    assert method in (None, "post")
    assert action in (None, "") or "location-search" in action


def test_phase_2y_native_layer_details_remains_toggleable():
    details = _by_id("geology-layer-details")
    assert details["tag"] == "details"
    assert details["attrs"].get("disabled") is None
    assert any(
        item["tag"] == "summary" and item["parent"]["attrs"].get("id") == "geology-layer-details"
        for item in _html_elements()
    )
    assert "geologyLayerDetails.addEventListener('toggle'" in MAP_JS
    assert not re.search(r"geologyLayerDetails[\s\S]{0,300}preventDefault\(\)", MAP_JS)
    assert not re.search(r"#geology-layer-details[^{}]*\{[^}]*pointer-events\s*:\s*none", CSS)
    assert not re.search(r"#geology-layer-details[^{}]*\{[^}]*display\s*:\s*none", CSS)


def test_phase_2y_required_observers_target_required_contract_elements():
    """Required observer targets must be present and explicitly named in JS."""
    assert 'id="cctv-layer"' in MAP_TEMPLATE
    assert 'id="layer-list"' in MAP_TEMPLATE
    assert ".observe($('cctv-layer')" in MAP_JS
    assert ".observe($('layer-list')" in MAP_JS


def test_phase_2y_optional_new_project_bindings_are_null_safe():
    assert "$('new-project-button').addEventListener" not in MAP_JS
    assert "$('new-project-form').addEventListener" not in MAP_JS
    assert "document.querySelector('[data-close-project]').addEventListener" not in MAP_JS
