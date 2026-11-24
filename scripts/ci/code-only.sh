#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

if [[ -e .env || -L .env || -e db.sqlite3 || -L db.sqlite3 || -d data/processed ]]; then
  echo "Use a Code-Only checkout without runtime data or local configuration." >&2
  exit 1
fi

python_bin=$(command -v "${PYTHON_BIN:-python3}")
node_bin=$(command -v "${NODE_BIN:-node}")
scratch=$(mktemp -d "${TMPDIR:-/tmp}/worksite-code-only.XXXXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
run=(env -i PATH="$PATH" TMPDIR="$scratch" PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
  DB_ENGINE=sqlite DB_NAME="$scratch/check.sqlite3"
  WORKSITE_TEST_SQLITE_PATH="$scratch/test.sqlite3" "$python_bin" -B)

"${run[@]}" scripts/ci/check-integrity.py
"${run[@]}" manage.py check
"${run[@]}" manage.py makemigrations --check --dry-run
"${run[@]}" demo.py --init-only
"${run[@]}" -m pytest -p no:cacheprovider --ds=worksite_gis.tests.worksite_test_settings --tb=short \
  --deselect=worksite_gis/tests/test_api.py::test_status_endpoint_reports_processed_data_validation \
  --deselect=worksite_gis/tests/test_data_outputs.py::test_nature_reserves_catalog_path_matches_existing_output \
  --deselect=worksite_gis/tests/test_data_outputs.py::test_mrt_stations_keep_only_station_codes \
  --deselect=worksite_gis/tests/test_data_outputs.py::test_mrt_lines_include_lrt_source \
  --deselect=worksite_gis/tests/test_data_outputs.py::test_mrt_stations_include_kaohsiung_lrt_csv_codes \
  --deselect=worksite_gis/tests/test_data_outputs.py::test_mrt_stations_have_display_names \
  --deselect=worksite_gis/tests/test_data_outputs.py::test_mrt_stations_include_imported_new_taipei_lines
"${run[@]}" docs/cases/synthetic-geological-screening/run.py
"$node_bin" --check home/static/home/js/map.js
"$node_bin" --check home/static/home/js/maintainer_plans.js
"$node_bin" --test worksite_gis/tests/test_cwa_ui.mjs
"$node_bin" tests/phase4-wra-ui.mjs
"$node_bin" docs/cases/synthetic-geological-screening/check-ui.mjs
