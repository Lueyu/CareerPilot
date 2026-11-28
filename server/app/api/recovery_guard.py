"""Protect private and registered API routes, including login and recovery."""
from app.api.errors import error_response
from app.services.recovery_control import RecoveryBlocked, require_normal


class RecoveryGuardMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope['path'].startswith('/api/'):
            try:
                await require_normal()
            except RecoveryBlocked as exc:
                return await error_response(scope, 503, exc.detail)(scope, receive, send)
        return await self.app(scope, receive, send)
