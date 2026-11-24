# Runtime profiles and verification boundaries

The [local demo](demo-runtime.md) uses an isolated SQLite workspace, local mapping assets and mock providers. It runs on loopback. The optional Django deployment profile is a separate way to check the application with `DEBUG=False`, WhiteNoise and Gunicorn. Hosted operation and live service behavior remain unverified.

## Isolated deployment checks

Install `requirements-deployment.txt` in a POSIX environment. Before selecting `config.deployment_settings`, configure a new high-entropy `SECRET_KEY`, explicit `DJANGO_ALLOWED_HOSTS`, a disposable database, and a separate `WORKSITE_STATIC_ROOT`. Use SQLite for this local exercise. The PostgreSQL settings path expects `DB_ENGINE=postgresql` with individual `DB_*` settings. This deployment profile rejects `DATABASE_URL` to prevent selecting the wrong database.

With the environment verified to point only to the disposable database and static directory, use these checks:

```sh
export DJANGO_SETTINGS_MODULE=config.deployment_settings
python manage.py check --deploy
python manage.py migrate --check
python manage.py collectstatic --noinput
gunicorn --check-config config.wsgi:application
gunicorn --forwarded-allow-ips= --bind 127.0.0.1:8001 config.wsgi:application
```

An empty disposable database first needs `python manage.py migrate --noinput`. The profile enables HTTPS redirects and secure cookies. Only configure `DJANGO_TRUST_HTTPS_PROXY` when a trusted reverse proxy clears and sets forwarding headers. HSTS requires a domain and TLS decision. Historical loopback verification left the domain-dependent HSTS warning open.

## Runtime status

- `/api/health/` checks liveness. `/api/ready/` checks the core database, schema and required static files. `/api/status/` reports availability of optional assets without establishing the quality of the supplied data.
- Alert reads are separate from the management command that synchronizes the upstream. WRA image responses bind their extent and image to a batch. TDX stale responses and browser media readiness have distinct status.
- Formal GIS and tiles, live government services, production PostgreSQL, trusted proxy setup, and shared caches under multi-worker hosting require separate verification. Keep secrets and identifying request data out of logs.

[Validation evidence and exclusions](validation-summary.md)

## Optional GIS import inputs

The GIS import tools are outside the Demo workflow and require separately authorized inputs and an isolated data tree. Run `python -m scripts.import_flood_scenarios --help` or `python -m scripts.import_mrt_station_additions --help` for the explicit source arguments. The latter uses TDX when executing an actual import. The portfolio supplies no credentials or formal input data.
