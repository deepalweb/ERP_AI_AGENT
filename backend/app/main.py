from fastapi import FastAPI

app = FastAPI(
    title="ERP AI Agent API",
    version="0.1.0",
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "erp-ai-agent",
    }


@app.get("/")
async def root():
    return {
        "message": "ERP AI Agent API",
        "docs": "/docs",
    }
