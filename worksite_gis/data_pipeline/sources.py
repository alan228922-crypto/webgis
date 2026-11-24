from __future__ import annotations

from pathlib import Path


VECTOR_SPECS = {
    "tra_stations": ("台灣軌道", "MARK_臺鐵車站_1150109", "MARK_臺鐵車站.shp"),
    "rail_lines": ("台灣軌道", "臺灣鐵路1150409", "RAIL_1150409.shp"),
    "mrt_stations": ("台灣軌道", "捷運1150409", "MARK_捷運車站_1150409.shp"),
    "mrt_lines": ("台灣軌道", "捷運車站1150409", "MRT_1150409.shp"),
    "highways": ("台灣軌道", "國道及省道(含快速公路以上等級)道路中線1150409", "ROAD_國省道(含快速公路)_1150409.shp"),
}


def discover_vector_sources(source_root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for slug, parts in VECTOR_SPECS.items():
        candidate = source_root.joinpath(*parts)
        if candidate.exists():
            found[slug] = candidate
    return found
