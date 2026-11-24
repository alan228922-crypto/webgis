"""Run the entirely synthetic geology case in an isolated Code-Only environment."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]


def main():
    if (ROOT / ".env").exists():
        raise SystemExit("Run the synthetic case in the Code-Only workspace without a .env file.")
    with tempfile.TemporaryDirectory(prefix="worksite-synthetic-screening-") as directory:
        directory = Path(directory)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1", "DJANGO_DEBUG": "True",
            "DB_ENGINE": "sqlite", "DB_NAME": str(directory / "app.sqlite3"),
            "WORKSITE_TEST_SQLITE_PATH": str(directory / "case.sqlite3"),
            "WORKSITE_DATA_ROOT": str(directory), "PYTHONPATH": str(ROOT),
        }
        return subprocess.call([
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "--ds=worksite_gis.tests.worksite_test_settings",
            "--basetemp", str(directory / "pytest"),
            "worksite_gis/tests/test_phase3_rtree.py",
            "worksite_gis/tests/test_phase3_geometry.py",
            "worksite_gis/tests/test_phase3_screening.py",
            "worksite_gis/tests/test_phase3_independent.py",
        ], cwd=ROOT, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
