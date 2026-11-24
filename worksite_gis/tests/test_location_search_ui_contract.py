from pathlib import Path
from html.parser import HTMLParser


MAP_TEMPLATE = Path("home/templates/home/map.html").read_text(encoding="utf-8")
MAP_JS = Path("home/static/home/js/map.js").read_text(encoding="utf-8")


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.form = None
        self.form_depth = 0
        self.query_inside_form = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "location-search-form":
            self.form = attrs
            self.form_depth = 1
            return
        if self.form_depth:
            self.form_depth += 1
            if tag == "input" and attrs.get("id") == "location-query":
                self.query_inside_form = True

    def handle_endtag(self, tag):
        if self.form_depth:
            self.form_depth -= 1


def _location_form():
    parser = _FormParser()
    parser.feed(MAP_TEMPLATE)
    return parser


def test_location_search_form_has_submit_contract_without_inline_override():
    assert '<form id="location-search-form"' in MAP_TEMPLATE
    assert 'id="location-query"' in MAP_TEMPLATE
    assert 'name="query"' in MAP_TEMPLATE
    assert 'method="post"' in MAP_TEMPLATE
    assert "{% url 'project-location-search' 'worksite' %}" in MAP_TEMPLATE
    assert '<button type="submit">' in MAP_TEMPLATE
    assert 'onsubmit="return false;"' not in MAP_TEMPLATE


def test_location_search_form_contains_its_own_django_csrf_token():
    form_html = MAP_TEMPLATE.split('<form id="location-search-form"', 1)[1].split('</form>', 1)[0]
    assert 'name="csrfmiddlewaretoken"' in form_html or "{% csrf_token %}" in form_html


def test_location_search_handler_validates_query_and_controls_loading_state():
    assert "value.trim()" in MAP_JS
    assert "請先輸入搜尋位置。" in MAP_JS
    assert "locationSearchButton.disabled = true" in MAP_JS
    assert "finally" in MAP_JS


def test_location_search_handler_preserves_post_csrf_and_handles_empty_candidates():
    assert "'/api/projects/' + currentSlug() + '/location-search/'" in MAP_JS
    assert "method: 'POST'" in MAP_JS
    assert "'X-CSRFToken': csrf()" in MAP_JS
    assert "找不到符合的位置。" in MAP_JS
    assert "data.candidates || []" in MAP_JS


def test_location_search_fetch_uses_same_origin_credentials_with_csrf_header():
    assert "'X-CSRFToken': csrf()" in MAP_JS
    assert "credentials: 'same-origin'" in MAP_JS


def test_location_search_errors_keep_http_status_for_user_messages():
    assert "error.status = response.status" in MAP_JS
    assert "請先登入後再搜尋。" in MAP_JS
    assert "目前沒有權限使用位置搜尋。" in MAP_JS
    assert "搜尋內容無效，請重新輸入。" in MAP_JS
    assert "位置搜尋服務暫時無法使用，請稍後再試。" in MAP_JS


def test_location_search_form_structure_matches_the_post_handler():
    parser = _location_form()
    assert parser.form is not None
    assert parser.query_inside_form
    assert parser.form.get("method") == "post"
    assert "location-search" in parser.form["action"]
    assert "on('location-search-form', 'submit'" in MAP_JS
    assert "method: 'POST'" in MAP_JS
    assert "body: JSON.stringify({ query })" in MAP_JS
