# Persistent backend startup for the daily scan scheduler to actually run.
# No --reload (that's for active development only) -- a long-running
# service process, restarted by Task Scheduler on crash.
Set-Location "C:\Celeste\erp-ai-agent"
& ".\.venv\Scripts\python.exe" -m uvicorn backend.app.main:app --port 8000 *>> "C:\Celeste\erp-ai-agent\backend_service.log"
