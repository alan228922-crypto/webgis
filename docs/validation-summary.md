# Validation scope

## Historical Code-Only acceptance

Phase 4 acceptance on 2026-11-24 used macOS 15.8.2 x86_64, Python 3.13.14, Django 5.2.17, SQLite 3.50.4, Node 24.22.1 and Chrome 158.0.8127.36.

| Check | Historical result |
| --- | --- |
| Full executable pytest selection | 538 collected, 531 passed, 7 deselected, 0 failed, 0 skipped, 2 warnings |
| Offline geology case | 23 passed, already included in the full executable selection |
| Django and schema | Django check passed. No migration drift. |
| Browser | Chrome A–O passed on loopback with synthetic data, Mock GeologyCloud and local static assets. No external HTTP requests were observed. |
| SQLite backup and restore | Restored an isolated snapshot with roles, workflow events and published v1 separate from private v2. |
| Local WSGI and static delivery | Gunicorn and WhiteNoise served static assets and readiness with `DEBUG=False` on loopback. |

Phase 2 acceptance exercised immutable revisions, rollback, SQLite write conflicts and publication changes. Phase 3 acceptance exercised projected distance, union area, completeness against an independent analytical reference, and fixed results across viewport changes. The [case study](cases/synthetic-geological-screening/README.md) describes the reference fixture.

## Local portfolio candidate

On macOS, the candidate collected 540 tests. 533 passed, 7 were deselected, none failed or skipped, and two existing pyogrio warnings reported synthetic sample data without a CRS. The two additional cases exercise the explicit source arguments for optional GIS import scripts. The offline case passed 23 tests in its separate run. Django check, migration drift, Node syntax and UI tests passed.

With Python 3.13 and Node 24 available, run `bash scripts/ci/code-only.sh` from the code-only repository to repeat the local check. The runner uses a temporary SQLite database. The hosted workflow installs dependencies before separating verification into a loopback-only Linux network namespace. Local results describe the macOS environment.

## Renamed portfolio candidate

The fresh macOS run collected 542 tests. 535 passed, 7 were deselected, none failed or skipped, and the existing two Pyogrio warnings reported synthetic sample data without a CRS. The 23-case offline runner, Django and migration checks, Node checks, and 16 Chrome scenarios passed. The earlier figures above describe the earlier milestone.

## Formal GIS exclusions

The following seven tests remain in the suite. The code-only environment excludes these exact node IDs because their licensed production assets are absent.

| pytest node ID | Required asset |
| --- | --- |
| `worksite_gis/tests/test_api.py::test_status_endpoint_reports_processed_data_validation` | Formal processed-data validation report |
| `worksite_gis/tests/test_data_outputs.py::test_nature_reserves_catalog_path_matches_existing_output` | Formal catalogue, validation report and nature reserve GeoJSON |
| `worksite_gis/tests/test_data_outputs.py::test_mrt_stations_keep_only_station_codes` | Formal MRT station GeoJSON and attributes |
| `worksite_gis/tests/test_data_outputs.py::test_mrt_lines_include_lrt_source` | Formal MRT and LRT route GeoJSON |
| `worksite_gis/tests/test_data_outputs.py::test_mrt_stations_include_kaohsiung_lrt_csv_codes` | Formal station GeoJSON with imported Kaohsiung LRT codes |
| `worksite_gis/tests/test_data_outputs.py::test_mrt_stations_have_display_names` | Formal station GeoJSON with display names |
| `worksite_gis/tests/test_data_outputs.py::test_mrt_stations_include_imported_new_taipei_lines` | Formal station GeoJSON with imported New Taipei lines |

## Verification boundary

Formal GIS, live government services, operational PostgreSQL, hosted deployment, and end-to-end operation on Linux and Windows remain **NOT VERIFIED**. The GitHub Actions workflow is configuration for the candidate. **GitHub Actions Runtime — NOT VERIFIED.** The historical results above apply to the Phase 4 baseline. Public release remains **BLOCKED**.
