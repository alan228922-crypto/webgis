"""Portable command-line contract for optional GIS source imports."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("module", "option", "diagnostic"),
    [
        ("import_flood_scenarios", "--source-root", "source directory not found"),
        ("import_mrt_station_additions", "--circular-source", "source file not found"),
    ],
)
def test_import_requires_an_explicit_source(module, option, diagnostic, tmp_path):
    root = Path(__file__).resolve().parents[1]
    command = [sys.executable, "-m", f"scripts.{module}"]
    help_result = subprocess.run(
        [*command, "--help"], cwd=root, capture_output=True, text=True, check=True
    )
    assert option in help_result.stdout
    missing = tmp_path / "missing-source"
    result = subprocess.run(
        [*command, option, str(missing)], cwd=root, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert diagnostic in result.stderr
    assert str(missing) in result.stderr
