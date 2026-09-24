"""Local-only development or a shared, server-configured operator login."""

import ipaddress
import os
import secrets

from fastapi import HTTPException, Request
from fastapi.security import HTTPBasic
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_basic = HTTPBasic(auto_error=False)


def auth_mode():
    return os.getenv("APP_AUTH_MODE", "local").lower()


def _loopback(host):
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host or "")
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_loopback
    except ValueError:
        return False


def _error(status, detail, headers=None):
    return JSONResponse({"status": "error", "detail": detail}, status_code=status, headers=headers)


class AccessControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        mode = auth_mode()
        if mode == "local":
            # Check the peer as well as Host to resist DNS rebinding. A
            # reverse proxy is not an authentication boundary in this mode.
            if not request.client or not _loopback(request.client.host) or not _loopback(request.url.hostname):
                return _error(403, "This app is configured for local access only.")
            origin = request.headers.get("origin")
            allowed = {
                value.strip() for value in os.getenv(
                    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
                ).split(",")
            }
            allowed.add(str(request.base_url).rstrip("/"))
            if origin and origin not in allowed:
                return _error(403, "This origin is not allowed.")
        elif mode == "basic":
            username = os.getenv("APP_AUTH_USERNAME", "")
            password = os.getenv("APP_AUTH_PASSWORD", "")
            if not username or not password:
                return _error(503, "Application access has not been configured.")
            if request.url.path != "/auth/status":
                try:
                    credentials = await _basic(request)
                except HTTPException:
                    credentials = None
                valid_user = secrets.compare_digest(
                    (credentials.username if credentials else "").encode(), username.encode()
                )
                valid_password = secrets.compare_digest(
                    (credentials.password if credentials else "").encode(), password.encode()
                )
                if not (valid_user and valid_password):
                    # The SPA provides its own login form; other clients
                    # receive the standard HTTP Basic challenge.
                    headers = {} if request.headers.get("x-requested-with") == "XMLHttpRequest" else {
                        "WWW-Authenticate": 'Basic realm="Celeste Intelligence"'
                    }
                    return _error(401, "Sign in to access Celeste.", headers)
        else:
            return _error(503, "Application access has not been configured.")

        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
