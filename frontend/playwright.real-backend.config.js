import { defineConfig } from "@playwright/test";
import { existsSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Controlled Pilot, Task 1: the one opt-in test that exercises the REAL
// stack end to end -- real Chromium, real `npm run dev` frontend build,
// real FastAPI server (uvicorn), the real read-only reporting database
// (the customer's live Odoo data), and an ISOLATED temp SQLite app
// database that this file creates fresh on every run and never reuses.
// Distinct from playwright.config.js (frontend/e2e/), which mocks every
// /api/** call and never starts a backend at all -- that suite stays the
// fast, deterministic default; this one is the slow, real-data companion,
// run explicitly with `npm run test:e2e:real`.
//
// Safety: REPORTING_DATABASE_URL is inherited from the real repo-root
// .env (the backend's own `load_dotenv()` picks it up -- this file never
// reads or echoes it) and is used strictly read-only by architecture
// (backend/app/db/reporting.py). APP_DATABASE_URL is overridden below to
// an isolated file under the OS temp dir -- every write this run makes
// (case sync, action saves) lands there, never in the repo's real app.db.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, "..");

// A FIXED (not randomized) path, deliberately: this config module is
// evaluated independently in more than one Node process per run (the
// orchestrator process that starts webServer, and the worker process
// that runs the spec file and imports these constants) -- each process
// gets its own module instance, so a per-evaluation random suffix
// (mkdtempSync) silently produces a DIFFERENT path in each, and the
// backend ends up writing to one path while the test asserts against
// another. A fixed path removes the mismatch: every process that
// evaluates this file agrees on the same string. Isolation from
// production doesn't depend on uniqueness -- it depends on this path
// never being the repo's real app.db, which a fixed path under the OS
// temp dir already guarantees just as well. The spec file's beforeAll
// deletes any stale file here before each run for a clean start.
const TEMP_DIR = path.join(tmpdir(), "celeste-real-e2e-appdb");
mkdirSync(TEMP_DIR, { recursive: true });
const APPDB_PATH = path.join(TEMP_DIR, "test_app.db").replace(/\\/g, "/");
export const APPDB_SQLITE_PATH = APPDB_PATH; // read back by the spec file to assert isolation (Task 4)
const APP_DATABASE_URL = `sqlite:///${APPDB_PATH}`;

const BACKEND_PORT = 8199;
const FRONTEND_PORT = 5190;
export const BACKEND_BASE_URL = `http://127.0.0.1:${BACKEND_PORT}`;

// A production app.db existing at the repo root is a normal, expected
// state (it's the real running app's own database) -- not evidence of
// anything wrong by itself. What Task 4 needs proven is that THIS run
// never touches it, which the spec file checks by snapshotting its mtime
// (if present) before the run and comparing after.
export const PROD_APPDB_PATH = path.join(REPO_ROOT, "app.db");
if (!existsSync(REPO_ROOT)) {
  throw new Error(`Expected repo root at ${REPO_ROOT} -- refusing to start with an unresolved path.`);
}

export default defineConfig({
  testDir: "./e2e-real-backend",
  outputDir: "./test-results-real",
  // Covers both a single real scan inside beforeAll (measured up to ~270s
  // end to end) and, per-test, up to two real scans plus real browser
  // interaction -- individual tests raise this further via test.setTimeout.
  timeout: 900_000,
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      // cwd is the repo root so `backend.app.main` resolves as a package
      // AND so the backend's own load_dotenv() finds the real .env there
      // -- this file never reads REPORTING_DATABASE_URL itself.
      command: `"${path.join(REPO_ROOT, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python")}" -m uvicorn backend.app.main:app --host 127.0.0.1 --port ${BACKEND_PORT}`,
      cwd: REPO_ROOT,
      env: {
        APP_DATABASE_URL: APP_DATABASE_URL,
        APP_SCAN_LOCK_PATH: `${APPDB_PATH}.scan.lock`,
        CORS_ORIGINS: `http://localhost:${FRONTEND_PORT}`,
        // The scheduler would otherwise fire a real 5am scan against the
        // isolated DB in the background -- irrelevant to this test and
        // just a source of noise/log confusion during a run.
        ENABLE_SCHEDULER: "false",
        // Enforce read-only reporting transactions in this test process.
        // The isolated application database is SQLite and is unaffected.
        PGOPTIONS: "-c default_transaction_read_only=on -c statement_timeout=90000",
        PGCONNECT_TIMEOUT: "10",
      },
      url: `${`http://127.0.0.1:${BACKEND_PORT}`}/health`,
      reuseExistingServer: false,
      timeout: 30_000, // backend process startup only -- the real scan happens per-request, not at boot
    },
    {
      command: `npm run dev -- --port ${FRONTEND_PORT} --strictPort`,
      env: {
        // Bypasses vite.config.js's hardcoded :8000 proxy target entirely
        // -- points the real frontend build straight at this run's real
        // backend port instead.
        VITE_API_BASE_URL: `http://127.0.0.1:${BACKEND_PORT}`,
      },
      url: `http://localhost:${FRONTEND_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
