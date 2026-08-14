from fastapi import FastAPI

app = FastAPI(title="MCP Server")


@app.get("/mcp/health")
async def health():
    return {"status": "ok", "service": "mcp-server"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("mcp_server.server:app", host="127.0.0.1", port=9000, reload=True)
