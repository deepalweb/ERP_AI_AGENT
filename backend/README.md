# backend

Python backend for the ERP AI Agent.

## Run

```
uvicorn backend.app.main:app --reload --port 8000
```

Requires `REPORTING_DATABASE_URL` and `OPENAI_API_KEY` set in the repo root
`.env` (see `.env.example`).
