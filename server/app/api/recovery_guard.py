"""Protect private and registered API routes, including login and recovery."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.services.recovery_control import RecoveryBlocked, require_normal


class RecoveryGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path.startswith('/api/'):
            try:
                await require_normal()
            except RecoveryBlocked as exc:
                return JSONResponse({"detail": exc.detail}, status_code=503)
        return await call_next(request)
