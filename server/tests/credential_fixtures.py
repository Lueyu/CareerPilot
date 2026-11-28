"""Explicit enrollment for synthetic test credentials, never a runtime bypass."""
from app.services.local_credentials import enroll


async def enroll_token(session, token, workspace=None):
    row = await enroll(session, token, workspace=workspace)
    await session.commit()
    return row
