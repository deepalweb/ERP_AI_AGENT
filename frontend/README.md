# frontend

React + Vite UI for Celeste Intelligence.

## Run

```
npm install
npm run dev
```

Opens on `http://localhost:5173`. Requests use `/api`; Vite proxies them to
the backend on `http://127.0.0.1:8000` (see `backend/README.md`).

For production, route `/api/*` on the frontend origin to the backend, stripping
the `/api` prefix. Alternatively, set `VITE_API_BASE_URL` to the API's HTTPS
origin at build time and add the frontend origin to backend `CORS_ORIGINS`.
See `.env.example`. The production host must also serve `index.html` for UI
routes such as `/actions` and `/dashboard`.

Local access does not need a login. When the backend enables shared login, the
UI prompts for its operator credentials. They stay in memory for this tab only;
reload and sign-out clear them. Never put credentials in `VITE_*` variables.

Checks: `npm test`, `npm run build`, and `npm run lint`.
