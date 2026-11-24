"""Scan candidate files without online validation or printing detected values."""

import hashlib
import json
import os
from pathlib import Path
import subprocess

from detect_secrets.core.scan import scan_file
from detect_secrets.settings import default_settings

ROOT = Path(__file__).resolve().parents[2]
REVIEW_FILE = Path("scripts/ci/reviewed-secret-findings.json")


def source_line_fingerprint(line):
    digest = hashlib.sha256(line.encode("utf-8")).hexdigest()
    return ":".join(digest[index:index + 8] for index in range(0, len(digest), 8))


def main():
    os.chdir(ROOT)
    reviewed = {
        (item["path"], item["type"], item["fingerprint"]): item["reason"]
        for item in json.loads(REVIEW_FILE.read_text(encoding="utf-8"))
    }
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    )
    paths = sorted({Path(os.fsdecode(name)) for name in names.split(b"\0") if name})
    known = 0
    unknown = 0
    with default_settings() as settings:
        settings.filters.pop("detect_secrets.filters.allowlist.is_line_allowlisted", None)
        for path in paths:
            if path.is_symlink() or not path.is_file():
                raise SystemExit(f"Missing or linked candidate file: {path}")
            for finding in scan_file(path.as_posix()):
                line = path.read_text(encoding="utf-8").splitlines()[finding.line_number - 1]
                key = (path.as_posix(), finding.type, source_line_fingerprint(line))
                if key in reviewed:
                    known += 1
                    print(f"Reviewed {path}:{finding.line_number} [{finding.type}]: {reviewed[key]}")
                else:
                    unknown += 1
                    print(f"Review required {path}:{finding.line_number} [{finding.type}]")
    print(f"Secret scan: {len(paths)} files, {known} reviewed, {unknown} unresolved")
    return int(unknown > 0)


if __name__ == "__main__":
    raise SystemExit(main())
