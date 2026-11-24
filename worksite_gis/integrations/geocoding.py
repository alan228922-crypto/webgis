"""Location search adapters."""

import requests

DEFAULT_HTTP_CLIENT = requests

class TgosGeocodingProvider:
    provider = "tgos"

    def __init__(self, api_key, url, client=None, timeout=10):
        self.api_key, self.url = api_key, url
        self.client = client
        self.timeout = timeout

    def search(self, query):
        if not self.url or not self.api_key:
            return []
        response = self.client.get(self.url, params={"keyword": query, "key": self.api_key}, headers={"Accept": "application/json", "User-Agent": "Worksite-GIS/1.0"}, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        records = payload.get("results") or payload.get("Result") or payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            return []
        output = []
        for item in records:
            if not isinstance(item, dict):
                continue
            try:
                lat = float(item.get("latitude", item.get("lat", item.get("Y"))))
                lon = float(item.get("longitude", item.get("lon", item.get("lng", item.get("X")))))
            except (TypeError, ValueError):
                continue
            output.append({"label": item.get("label") or item.get("name") or item.get("Name") or query, "address": item.get("address") or item.get("Address") or "", "latitude": lat, "longitude": lon, "provider": self.provider, "precision": item.get("precision", "address"), "raw_id": str(item.get("id") or item.get("ID") or item.get("raw_id") or "")})
        return output


class NlscGeocodingProvider:
    """國土測繪中心圖資服務雲的可設定搜尋介面。"""

    provider = "nlsc"

    def __init__(self, url, token="", client=None, timeout=10):
        self.url, self.token = url, token
        self.client = client or DEFAULT_HTTP_CLIENT
        self.timeout = timeout

    def search(self, query):
        if not self.url:
            return []
        params = {"query": query, "q": query, "keyword": query}
        headers = {"Accept": "application/json", "User-Agent": "Worksite-GIS/1.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = self.client.get(self.url, params=params, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            records = payload.get("results") or payload.get("data") or payload.get("features") or payload.get("items") or []
        else:
            records = payload
        output = []
        for item in records if isinstance(records, list) else []:
            if not isinstance(item, dict):
                continue
            props = item.get("properties") if isinstance(item.get("properties"), dict) else item
            geometry = item.get("geometry") if isinstance(item.get("geometry"), dict) else {}
            coords = geometry.get("coordinates") if isinstance(geometry.get("coordinates"), list) else []
            try:
                lat = float(props.get("latitude", props.get("lat", props.get("Y", coords[1] if len(coords) > 1 else None))))
                lon = float(props.get("longitude", props.get("lon", props.get("lng", props.get("X", coords[0] if coords else None)))))
            except (TypeError, ValueError, IndexError):
                continue
            output.append({"label": props.get("label") or props.get("name") or props.get("Name") or query, "address": props.get("address") or props.get("Address") or "", "latitude": lat, "longitude": lon, "provider": self.provider, "precision": props.get("precision", "address"), "raw_id": str(props.get("id") or props.get("ID") or props.get("raw_id") or "")})
        return output[:10]


class NominatimGeocodingProvider:
    provider = "nominatim"

    def __init__(self, url, client=None, timeout=10):
        self.url = url
        if client is not None:
            self.client = client
        else:
            # Nominatim must not inherit a dead localhost proxy from the
            # shell environment; callers can still inject a client in tests.
            if hasattr(DEFAULT_HTTP_CLIENT, "Session"):
                self.client = DEFAULT_HTTP_CLIENT.Session()
                self.client.trust_env = False
            else:
                self.client = DEFAULT_HTTP_CLIENT
        self.timeout = timeout

    def search(self, query):
        if not self.url:
            return []
        response = self.client.get(
            self.url,
            params={
                "q": query,
                "format": "jsonv2",
                "addressdetails": 1,
                "limit": 10,
                "accept-language": "zh-TW,en",
                "countrycodes": "tw",
            },
            headers={"Accept": "application/json", "User-Agent": "Worksite-GIS/1.0"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return []
        output = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                lat = float(item.get("lat"))
                lon = float(item.get("lon"))
            except (TypeError, ValueError):
                continue
            label = item.get("name") or item.get("display_name") or query
            address = item.get("display_name") or label
            raw_id = ":".join(part for part in (str(item.get("osm_type") or ""), str(item.get("osm_id") or "")) if part)
            output.append({
                "label": label,
                "address": address,
                "latitude": lat,
                "longitude": lon,
                "provider": self.provider,
                "precision": item.get("type") or item.get("class") or "address",
                "raw_id": raw_id,
            })
        return output[:10]
