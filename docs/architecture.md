# Architecture and engineering methods

[Portfolio overview and architecture diagram](../README.md)

## Data and authority

| Layer | Responsibility |
| --- | --- |
| [Leaflet map](../home/templates/home/map.html) | Displays a selected project and its permitted plans. The browser makes JSON and GeoJSON requests and distinguishes viewport exploration from fixed-worksite screening. |
| [Django API](../worksite_gis/urls.py) | Resolves sessions, project access, worksite permissions, data routes and adapter boundaries. |
| [Permissions](../worksite_gis/permissions.py) and [workflow views](../worksite_gis/views_plans.py) | Restrict private edits and review details to authorized users. A content update requires the expected revision token. Publication and selection of the screening source are explicit operations. |
| [Models](../worksite_gis/models.py) and [publication service](../worksite_gis/services/worksite_publication.py) | Maintain PlanVersion content, workflow events, and the published pointer. Readers and screening share the selected published source while an authorized owner edits a draft. Archiving that source stops screening until a replacement is selected. |
| [Screening service](../worksite_gis/services/geology_screening.py) | Binds cache entries and results to published version, geometry, query area and analysis parameters. It reports layer availability, temporal provenance and completeness. |
| [Spatial index](../worksite_gis/data_pipeline/spatial_index.py) | Uses SQLite RTree envelope filtering, then exact predicates against original WKB. Display geometry may be simplified or clipped. The scan budget and display limit have separate flags. |
| [Demo settings](../config/demo_settings.py) | Use an isolated application SQLite database and local synthetic adapters. Real providers and remote basemaps are disabled in the demonstration. |

## Geometry pipeline

1. Validate GeoJSON coordinates in WGS84 longitude–latitude order. The case uses EPSG:3826 for metre-based distance and area within the relevant Taiwan projection zone.
2. Resolve the published snapshot and expand its polygon by the requested search distance. A conservative WGS84 envelope bounds candidate retrieval. Features that touch the closed boundary count as intersecting.
3. Refine candidates with original geometry, reporting intersection with the worksite and minimum distance. A feature can fall within the buffer while remaining disjoint from the worksite. For example, the synthetic rockfall feature lies 15 m away.
4. Intersect liquefaction features with the worksite and union the pieces to avoid double-counting overlapping polygons. Divide by worksite area only after the candidate set and coverage evidence meet the completeness conditions. Preserve `null` when those conditions are unknown.

The fixed screening context holds the version, geometry and buffer constant through viewport changes. The historical tests also exercise private revisions, publication, archive handling and the rejection of inconsistent or stale browser responses. [Independent synthetic calculations](cases/synthetic-geological-screening/README.md)

## Runtime boundaries

The verified synthetic path uses local assets, SQLite and mock geological data. Optional adapters exist for GeologyCloud, TDX, CWA and WRA. Official data contents, upstream coverage, multi-worker operation, PostgreSQL integration and hosted deployment are **NOT VERIFIED**. The app distinguishes unknown source publication time, fetch time and result generation time. A cached or stale result retains its status.

Migration 0014 adds workflow events and publishing references. Reverting it can remove event records and relationships. Recover an isolated database from a consistent backup with a compatible application version. [Demo backup procedure](demo-runtime.md) · [Deployment profile and scope](deployment-profile.md)
