# backend

Python backend for the ERP AI Agent.

## Run

```
uvicorn backend.app.main:app --reload --port 8000
```

Install dependencies with `python -m pip install -e ".[dev]"`. Configure
`REPORTING_DATABASE_URL` and your model provider in the repo root `.env`
(see `.env.example`). Application state defaults to `app.db`, separate from Odoo.

## Access

`APP_AUTH_MODE=local` is the default. It accepts only loopback clients with a
localhost/loopback Host header. Keep this mode bound to `127.0.0.1`; do not put it
behind a public reverse proxy. `/health` is public for health checks.

For shared access, set `APP_AUTH_MODE=basic`, `APP_AUTH_USERNAME`, and a strong
ASCII `APP_AUTH_PASSWORD` on the server. Missing credentials fail closed. The
frontend displays a login form and sends HTTP Basic credentials over each API
request. Use HTTPS for shared access. This is one shared operator account, not
individual users or role-based permissions. Credentials stay in tab memory;
reload or sign-out requires signing in again. API docs and business endpoints
are protected too. Do not place these credentials in `VITE_*` variables.

`CORS_ORIGINS` is a comma-separated allowlist when the frontend uses a different
origin. The default allows the two local Vite origins. Configure trusted proxy
headers only for your actual proxy; do not trust arbitrary forwarded client IPs.

## Scheduled and requested scans

The daily briefing runs at 05:00 in `APP_TIMEZONE` (default `Asia/Colombo`). Set
`ENABLE_SCHEDULER=false` on processes that should not schedule jobs.

The scheduler, `/investigations`, and stale `/brief` requests share the full
scan pipeline. A file lock serializes scans across threads and processes, and
waiting callers reuse a result completed during their wait. The lock defaults
to a separate `.scan.lock` file beside the app database, and waits up to ten
minutes before failing. Its implementation uses
[filelock](https://py-filelock.readthedocs.io/en/stable/).

Workers using the same app state must share that lock file. Set
`APP_SCAN_LOCK_PATH` to a common absolute path if needed. This setup coordinates
processes sharing a filesystem; separate hosts with independent filesystems
require a distributed coordinator. A fresh cached brief can still be read while
a scheduled refresh runs.

Run tests with `python -m pytest -q`. Scan tests isolate app state and stub Odoo
and model calls, including a regression involving two separate worker processes.
