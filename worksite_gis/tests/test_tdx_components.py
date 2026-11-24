import httpx
import pytest

from worksite_gis.integrations.tdx import TdxClient
from worksite_gis.integrations.tdx_normalizers import _items, _localized, _train_type_name
from worksite_gis.integrations.tdx_transport import TdxTransport


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def response(payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "https://example.test"))


def test_tdx_client_import_and_transport_token_cache():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response({"ok": True}),
    ])
    transport = TdxTransport("id", "secret", client=client)

    assert isinstance(TdxClient("id", "secret", client=client), TdxClient)
    assert transport.get_token() == "token"
    assert transport.get_token() == "token"
    payload = transport.get("https://example.test/data")

    assert payload == {"ok": True}
    assert [call[0] for call in client.calls] == ["post", "get"]
    assert client.calls[1][2]["params"] == {"$format": "JSON"}
    assert client.calls[1][2]["headers"] == {"Authorization": "Bearer token"}


def test_tdx_transport_preserves_http_and_json_errors():
    http_client = FakeClient([response({"error": "bad"}, status=503)])
    transport = TdxTransport("id", "secret", client=http_client)
    with pytest.raises(httpx.HTTPStatusError):
        transport.get("https://example.test/data")

    invalid = httpx.Response(200, content=b"not-json", request=httpx.Request("GET", "https://example.test"))
    json_client = FakeClient([response({"access_token": "token", "expires_in": 3600}), invalid])
    with pytest.raises(ValueError):
        TdxTransport("id", "secret", client=json_client).get("https://example.test/data")


def test_tdx_client_reuses_token_after_data_request_timeout():
    timeout = httpx.ReadTimeout("station live timeout", request=httpx.Request("GET", "https://example.test"))
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        timeout,
        response({"DailyTrainTimetables": []}),
    ])
    adapter = TdxClient("id", "secret", client=client)

    with pytest.raises(httpx.ReadTimeout):
        adapter._get("https://example.test/live")
    assert adapter._get("https://example.test/timetable") == {"DailyTrainTimetables": []}
    assert [call[0] for call in client.calls] == ["post", "get", "get"]
    assert client.calls[2][2]["headers"] == {"Authorization": "Bearer token"}


def test_tdx_normalizers_preserve_envelopes_and_fallbacks():
    assert _items([{"id": 1}], "Items") == [{"id": 1}]
    assert _items({"Items": [{"id": 1}]}, "Items") == [{"id": 1}]
    assert _items({"Unknown": [{"id": 1}]}, "Items") == []
    assert _localized({"Zh_tw": "中文", "En": "English"}) == "中文"
    assert _localized({"En": "English"}) == "English"
    assert _train_type_name("3") == "區間車"
    assert _train_type_name("unknown") == "臺鐵列車"


def test_tdx_client_public_dispatch_and_adapter_result_shape():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response({"CCTVs": []}),
    ])
    adapter = TdxClient("id", "secret", client=client)
    adapter.kind = "cctv"
    result = adapter.fetch()

    assert result.status == "ok"
    assert result.source == "tdx"
    assert result.records == []
    assert result.stale is False
    assert result.error == ""
