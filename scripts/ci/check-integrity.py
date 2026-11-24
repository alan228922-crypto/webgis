"""Verify the synthetic inputs and exact bundled frontend assets."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASE = ROOT / "docs/cases/synthetic-geological-screening"
VENDOR = ROOT / "home/static/home/vendor"


def verify(directory, expected):
    for name, digest in sorted(expected.items()):
        asset = directory / name
        if not asset.is_file() or asset.is_symlink():
            raise SystemExit(f"Missing or linked required asset: {asset.relative_to(ROOT)}")
        if hashlib.sha256(asset.read_bytes()).hexdigest() != digest:
            raise SystemExit(f"SHA-256 mismatch: {asset.relative_to(ROOT)}")


def main():
    fixtures = json.loads((CASE / "manifest.json").read_text(encoding="utf-8"))["sha256"]
    if set(fixtures) != {"fixtures.json", "expected.json"}:
        raise SystemExit("Unexpected synthetic input manifest")
    verify(CASE, fixtures)
    vendor = json.loads(
        (VENDOR / "integrity.json").read_text(encoding="utf-8")
    )["sha256"]
    actual = {
        file.relative_to(VENDOR).as_posix()
        for directory in ("geoman", "leaflet")
        for file in (VENDOR / directory).rglob("*")
        if file.is_file() or file.is_symlink()
    }
    if actual != set(vendor):
        raise SystemExit("Unexpected Leaflet or Geoman asset inventory")
    verify(VENDOR, vendor)
    print(f"Verified {len(fixtures)} synthetic fixture files and {len(vendor)} bundled assets")


if __name__ == "__main__":
    main()
