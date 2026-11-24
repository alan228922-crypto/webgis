from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from copy import deepcopy
from datetime import datetime, timezone
from inspect import signature

from shapely.geometry import shape
from shapely.ops import transform

from worksite_gis.integrations.geology_cloud import GEOLOGY_LAYERS
from worksite_gis.services.geology_screening import (
    GeologyCloudService,
    quality_metadata,
    polygon_overlap,
    _project,
    _worksite_union,
)


from worksite_gis.services.worksite_publication import geology_source


SUMMARY_LAYERS = ("drill", "liquefaction", "dip_slope", "rock_fall", "debris_flow_track")
SUMMARY_LAYER_TIMEOUT_SECONDS = 5
SUMMARY_TOTAL_DEADLINE_SECONDS = 8

LIMITATIONS = [
    "本摘要僅供工區工程地質條件初步檢核，不代表正式地質鑑定或施工安全判定。",
    "空間相交與最近距離不代表工程影響距離。",
]


class GeologySummaryService:
    def __init__(self, *, client=None):
        self.layer_service = GeologyCloudService(client=client, timeout=SUMMARY_LAYER_TIMEOUT_SECONDS)

    def _prepare_cache_context(self, layer, *, bbox, project, limit, publication, analysis=None):
        prepare = getattr(self.layer_service, "prepare_cache_context", None)
        if prepare is None:
            return {"cache_key": f"summary-context:{project.slug}:{layer}:{bbox}:{limit}", "fresh": None, "stale": None}
        return prepare(layer, bbox=bbox, project=project, limit=limit, publication=publication,
                       **({"analysis": analysis} if analysis else {}))

    def _fetch_layer(self, layer, *, bbox, project, limit, cache_context):
        fetch = self.layer_service.fetch_layer
        if "cache_context" in signature(fetch).parameters:
            return fetch(layer, bbox=bbox, project=project, limit=limit, cache_context=cache_context)
        return fetch(layer, bbox=bbox, project=project, limit=limit)

    def _persist_cache_write(self, cache_write):
        persist = getattr(self.layer_service, "persist_cache_write", None)
        if persist is not None:
            persist(cache_write)

    @staticmethod
    def liquefaction_metrics(payload, worksite_geometry):
        worksite = _worksite_union(worksite_geometry)
        if worksite is None or worksite.is_empty:
            return polygon_overlap(None, ())
        polygons = (
            transform(_project, shape(item["geometry"]))
            for item in payload.get("features", []) if item.get("geometry")
        )
        return polygon_overlap(transform(_project, worksite), polygons)

    @staticmethod
    def _unavailable_layer(layer, *, reason="no_worksite_geometry"):
        return {
            "layer": layer,
            "endpoint": GEOLOGY_LAYERS[layer]["endpoint"],
            "status": reason,
            "fetched_at": None,
            "candidate_feature_count": 0,
            "returned_feature_count": 0,
            "screening": {"status": reason},
            "error": None,
            "data_gap": reason,
        }

    def _layer_item(self, layer, response, limit):
        response = response or {}
        screening = deepcopy(response.get("screening") or {})
        status = response.get("status", "error")
        if screening.get("status") == "no_worksite_geometry":
            status = "no_worksite_geometry"
        item = {
            "layer": layer,
            "endpoint": GEOLOGY_LAYERS[layer]["endpoint"],
            "status": status,
            "fetched_at": response.get("fetched_at"),
            "candidate_feature_count": response.get("candidate_feature_count", len(response.get("features") or [])),
            "returned_feature_count": response.get("returned_feature_count", min(len(response.get("features") or []), limit)),
            "screening": screening,
            "error": response.get("error"),
            "data_gap": None,
        }
        item["screening"].setdefault("nearby_feature_count_scope", "candidate_features")
        if status in {"unavailable", "no_worksite_geometry"}:
            item["data_gap"] = response.get("error") or status
        if layer == "liquefaction":
            screening.setdefault("intersection_area_m2", None)
            screening.setdefault("intersection_ratio", None)
            screening.setdefault("overlap_reason", item["data_gap"] or "analysis_input_incomplete")
        item["data_quality"] = response.get("data_quality") or quality_metadata(
            "error" if status == "error" else "unavailable", reason=response.get("failure_reason") or response.get("error"),
        )
        return item

    @staticmethod
    def _actions(layers, *, geometry_available):
        actions = []
        if not geometry_available:
            actions.append({
                "id": "confirm-worksite-geometry",
                "label": "確認工區範圍",
                "reason": "目前沒有正式工區 geometry，無法進行空間檢核。",
                "severity": "information",
            })
        for item in layers:
            if item["status"] == "error":
                actions.append({
                    "id": f"retry-{item['layer']}",
                    "label": f"重新取得{item['endpoint']}資料",
                    "reason": item.get("error") or "圖層資料取得失敗。",
                    "severity": "attention",
                })
            elif item["status"] == "stale":
                actions.append({
                    "id": f"review-{item['layer']}-stale",
                    "label": f"確認{item['endpoint']}資料新鮮度",
                    "reason": "目前使用過期快取資料，應於 upstream 恢復後重新確認。",
                    "severity": "attention",
                })
        return actions

    def build(self, project, *, bbox, limit, publication=None, analysis=None, explicit_mode=False):
        publication = publication if publication is not None else geology_source(project)
        source, reason = publication
        worksite = source.geometry if source else None
        if worksite is None:
            layers = [self._unavailable_layer(layer) for layer in SUMMARY_LAYERS]
            for item in layers:
                item["data_gap"] = reason
                item["data_quality"] = quality_metadata("unavailable", reason=reason)
        else:
            layers = []
            cache_contexts = {
                layer: self._prepare_cache_context(layer, bbox=bbox, project=project, limit=limit, publication=publication,
                                                  **({"analysis": analysis} if analysis else {}))
                for layer in SUMMARY_LAYERS
            }
            executor = ThreadPoolExecutor(max_workers=len(SUMMARY_LAYERS))
            try:
                futures = {layer: executor.submit(self._fetch_layer, layer, bbox=bbox, project=project, limit=limit, cache_context=cache_contexts[layer]) for layer in SUMMARY_LAYERS}
                done, _ = wait(futures.values(), timeout=SUMMARY_TOTAL_DEADLINE_SECONDS)
                for layer in SUMMARY_LAYERS:
                    future = futures[layer]
                    if future not in done:
                        response = {"status": "error", "fetched_at": None, "features": [], "screening": {"status": "error"}, "error": "TimeoutError: summary layer deadline exceeded", "failure_reason": "timeout"}
                    else:
                        try:
                            response = future.result()
                            self._persist_cache_write(response.pop("_cache_write", None))
                        except Exception as exc:  # individual layer isolation
                            response = {"status": "error", "fetched_at": None, "features": [], "screening": {"status": "error"}, "error": f"{type(exc).__name__}: {str(exc)[:240]}"}
                    layers.append(self._layer_item(layer, response, limit))
            finally:
                executor.shutdown(wait=True, cancel_futures=True)

        if not explicit_mode:
            for item in layers:
                item.pop("data_quality", None)
        statuses = [item["status"] for item in layers]
        failed = {"stale", "error", "unavailable", "no_worksite_geometry"}
        if not worksite:
            summary_status = "unavailable"
        elif any(status in failed for status in statuses):
            summary_status = "partial"
        else:
            summary_status = "complete"

        successful = sum(status in {"live", "cached"} for status in statuses)
        data_layers = sum(item["candidate_feature_count"] > 0 for item in layers)
        intersecting = sum(item["screening"].get("intersects") is True for item in layers)
        values = {
            "requested_layers": len(layers),
            "successful_layers": successful,
            "data_layers": data_layers,
            "intersecting_layers": intersecting,
            "empty_layers": sum(item["candidate_feature_count"] == 0 for item in layers),
            "stale_layers": statuses.count("stale"),
            "error_layers": statuses.count("error"),
            "unavailable_layers": statuses.count("unavailable") + statuses.count("no_worksite_geometry"),
            "manual_review_layers": sum(status in {"stale", "error", "unavailable", "no_worksite_geometry"} for status in statuses),
        }
        drill = next(item for item in layers if item["layer"] == "drill")
        liquefaction = next(item for item in layers if item["layer"] == "liquefaction")
        slope = next(item for item in layers if item["layer"] == "dip_slope")
        rockfall = next(item for item in layers if item["layer"] == "rock_fall")
        debris = next(item for item in layers if item["layer"] == "debris_flow_track")
        cards = [
            {"id": "worksite-geology-overview", "status": "available" if worksite else "unavailable", "title": "工區地質檢核總覽", "values": values, "actions": [], "limitations": LIMITATIONS[:1]},
            {"id": "nearby-drill-evidence", "status": drill["status"], "title": "附近工程鑽探證據", "values": {"nearby_feature_count": drill["screening"].get("nearby_feature_count", 0), "nearest_distance_m": drill["screening"].get("nearest_distance_m"), "nearest_drill": drill["screening"].get("nearest_drill")}, "actions": [], "limitations": ["此卡片只代表附近存在工程鑽探點，不代表已取得地下地層完整資料。"]},
            {"id": "liquefaction-spatial-prompt", "status": liquefaction["status"], "title": "液化潛勢空間提示", "values": {key: liquefaction["screening"].get(key) for key in ("intersects", "intersection_area_m2", "intersection_ratio", "nearest_distance_m", "nearby_feature_count")}, "actions": [], "limitations": ["此結果為區域液化潛勢資料之空間提示，不代表現地液化安全判定。"]},
            {"id": "slope-rockfall-prompt", "status": "available" if slope["status"] in {"live", "cached"} and rockfall["status"] in {"live", "cached"} else "partial", "title": "坡地與落石條件提示", "values": {"dip_slope": slope["screening"], "rock_fall": rockfall["screening"]}, "actions": [], "limitations": ["此結果只表示與官方圖層的空間關係，不代表邊坡安全或落石發生機率。"]},
            {"id": "debris-flow-environment-prompt", "status": debris["status"], "title": "土石流環境提示", "values": debris["screening"], "actions": [], "limitations": ["此結果只表示與官方圖層的空間關係，不代表土石流發生機率或施工安全判定。"]},
            {"id": "data-credibility-service-status", "status": "available", "title": "資料可信度與服務狀態", "values": {"layers": layers}, "actions": [], "limitations": []},
        ]
        return {"project": {"slug": project.slug, "name": project.name, "geometry_available": bool(worksite)}, "status": summary_status, "generated_at": datetime.now(timezone.utc).isoformat(), "cards": cards, "layers": layers, "actions": self._actions(layers, geometry_available=bool(worksite)), "limitations": LIMITATIONS}
