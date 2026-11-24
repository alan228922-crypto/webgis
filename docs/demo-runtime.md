# Local synthetic demonstration

**Synthetic Data · Mock GeologyCloud · Preliminary screening**

The demo runs only on loopback. It generates an isolated SQLite database and local credentials, and serves Leaflet and Geoman from the repository. Geology and place search use synthetic responses. TDX, CWA, WRA, external media and remote maps are disabled.

## Setup and initialization

Start with a fresh workspace for this edition. Its app label, tables, role names, project slug and environment markers use the new namespace. Use the matching earlier application to open earlier demo databases.

From the Code-Only repository root, without a private `.env` or formal GIS:

```sh
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python demo.py
```

On Windows PowerShell the corresponding commands are below. The Windows runtime has yet to be verified end to end.

```powershell
py -3.13 -m venv .venv
$python = ".\.venv\Scripts\python.exe"
& $python -m pip install -r requirements.txt
& $python demo.py
```

If dependencies were acquired in advance, install them from a reviewed wheelhouse with `--no-index --find-links`. The launcher prints a private, newly generated data directory. Open `http://127.0.0.1:8000/` and read the local `accounts.json` for the worksite owner, reviewer and reader credentials. Keep this file and the database outside the repository. Use `python demo.py --init-only` to seed a workspace without starting the server.

## Walkthrough

1. As the reader, open **Synthetic Worksite** to view and export published v1.
2. As the owner, edit or create a worksite, then submit it. As the reviewer, open the maintenance view to return the submission with a reason or publish it. Confirm that readers retain public v1 while a later draft remains private.
3. Under the published-worksite geology check, enter **50 m** and inspect the five layer results. The default complete scenario shows **6,250 m² / 62.5%** liquefaction overlap.
4. Stop the server with Ctrl+C. Restart with `python demo.py --directory "<launcher-created directory>" --scenario unknown`. The same geometry produces **Cannot Calculate** for coverage because evidence is incomplete. Use `--scenario normal` to return to the baseline. Additional scenarios are `timeout`, `stale` and `empty`.
5. Use `/api/health/` for liveness and `/api/ready/` for readiness. Pass `--port 8001` if the default port is occupied. The launcher validates any reused directory. Sign in again after restarting.

[Offline runner and analytical reference](cases/synthetic-geological-screening/README.md)

## Stop, restart and back up

Stop the process with Ctrl+C. Keep the launcher-created workspace private and use `--directory` with its path to resume. The seed operation preserves existing edits.

For a consistent snapshot, open the demo database read-only and use the SQLite Backup API to write a separate restricted backup. Verify `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, a SHA-256 digest and the application version. Store the generated accounts file privately, outside the repository and any shared evidence bundle.

For a restore rehearsal, initialize a separate workspace, stop its process, and use the SQLite Backup API to write the snapshot into the launcher-created SQLite file while preserving its inode. Restore the matching private account file and permissions. Reopen through the launcher and check readiness, roles, events, published revisions and private drafts. Keep Migration 0014 when restoring databases containing workflow events.
