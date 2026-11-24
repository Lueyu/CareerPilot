"""Native token-authenticated product feedback."""
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_workspace
from app.api.product_feedback import Database, FeedbackRoute, submit
from app.models.workspace import Workspace

router = APIRouter(route_class=FeedbackRoute)


@router.post("/product-feedback")
async def native_feedback(request: Request, db: Database, workspace: Annotated[Workspace, Depends(get_workspace)]):
    return await submit(request, db, workspace.id)
