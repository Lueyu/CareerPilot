"""Registered gateway product feedback."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.i18n import message as localized_message
from app.api.product_feedback import Database, FeedbackRoute, submit
from app.api.public_auth import current_workspace
from app.models.workspace import Workspace

router = APIRouter(route_class=FeedbackRoute)


@router.post("/product-feedback")
async def public_feedback(request: Request, db: Database, workspace: Annotated[Workspace, Depends(current_workspace)]):
    if request.headers.getlist("x-expected-workspace") != [str(workspace.id)]:
        raise HTTPException(409, detail={"code": "workspace_changed", "message": localized_message("errors.refresh_the_page_before_submitting")})
    return await submit(request, db, workspace.id)
