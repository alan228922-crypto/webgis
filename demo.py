"""Initialize and run the loopback-only Synthetic Data demonstration."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True

from config.demo_paths import create_demo_directory, validate_demo_directory


ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, help="Reuse an existing launcher-created Demo workspace")
    parser.add_argument("--init-only", action="store_true", help="Initialize the Demo SQLite database and exit")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--scenario", choices=("normal", "unknown", "timeout", "stale", "empty"), default="normal")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if any((ROOT / name).exists() or (ROOT / name).is_symlink() for name in (".env", "data/source", "data/processed")):
        parser.error("run from a Code-Only tree without formal environment or GIS data")
    try:
        os.umask(0o077)
        directory = validate_demo_directory(args.directory) if args.directory else create_demo_directory()
    except (OSError, ValueError):
        parser.error("Demo workspace is invalid, unsafe, or does not belong to the launcher")

    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP") if key in os.environ}
    environment.update(
        DJANGO_SETTINGS_MODULE="config.demo_settings",
        WORKSITE_DEMO_DIR=str(directory),
        WORKSITE_DEMO_SCENARIO=args.scenario,
        PYTHONDONTWRITEBYTECODE="1",
    )

    def django(*command):
        subprocess.run([sys.executable, "-B", str(ROOT / "manage.py"), *command],
                       cwd=ROOT, env=environment, check=True)

    print(f"Synthetic Data workspace: {directory}", flush=True)
    try:
        django("migrate", "--noinput")
        django("seed_demo")
        print(f"Local account file: {directory / 'accounts.json'}", flush=True)
        if not args.init_only:
            django("runserver", f"127.0.0.1:{args.port}", "--noreload")
    except KeyboardInterrupt:
        return 130
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
