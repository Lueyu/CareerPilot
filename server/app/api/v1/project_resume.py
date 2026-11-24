from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import message as localized_message
from app.api.deps import get_workspace
from app.api.v1.project_autopilot import freshness, valid_report
from app.db import get_session
from app.models.project import Project
from app.models.project_analysis import ProjectAnalysis
from app.models.project_resume import ProjectResumeDraft
from app.models.workspace import Workspace
from app.services.project_context import project_profile

router = APIRouter()


class SaveResumeDraft(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)

    project_id: int = Field(gt=0)
    revision: int = Field(ge=0)
    contribution: str = Field(min_length=1, max_length=5000)
    outcome: str = Field(default='', max_length=5000)
    confirmed: bool

    @field_validator('confirmed')
    @classmethod
    def require_confirmation(cls, value: bool) -> bool:
        if not value:
            raise ValueError('请先确认是本人完成的工作')
        return value

    @field_validator('contribution', 'outcome')
    @classmethod
    def reject_null_characters(cls, value: str) -> str:
        if '\x00' in value:
            raise ValueError('个人陈述不能包含空字符')
        return value


async def _project(session: AsyncSession, workspace_id: int, project_id: int, *, lock: bool = False) -> Project:
    query = select(Project).where(
        Project.workspace_id == workspace_id, Project.id == project_id, Project.archived_at.is_(None),
    )
    project = await session.scalar(query.with_for_update() if lock else query)
    if project is None:
        raise HTTPException(404, localized_message("errors.project_not_found"))
    return project


def _draft_view(draft: ProjectResumeDraft | None) -> dict | None:
    if draft is None:
        return None
    return {
        'revision': draft.revision, 'contribution': draft.contribution, 'outcome': draft.outcome,
        'content': draft.content, 'updated_at': draft.updated_at.isoformat(),
    }


async def _repository_material(session: AsyncSession, project: Project) -> tuple[list[dict], list[dict]]:
    report = await valid_report(session, project)
    if report is None:
        report = await session.scalar(select(ProjectAnalysis).where(
            ProjectAnalysis.workspace_id == project.workspace_id, ProjectAnalysis.project_id == project.id,
            ProjectAnalysis.pipeline_version == 'legacy', ProjectAnalysis.status == 'SUCCESS',
            ProjectAnalysis.report.is_not(None),
        ).order_by(ProjectAnalysis.id.desc()).limit(1))
    evidence, suggestions = [], []
    if report and report.report:
        source = {'kind': 'analysis', 'analysis_id': report.id, 'revision': report.source_revision,
                  'freshness': freshness(project, report)}
        for claim in report.report.get('claims', []):
            if claim.get('kind') != 'CODE_OBSERVED' or not claim.get('citations') or not claim.get('text'):
                continue
            evidence.append({
                'id': f"analysis-{report.id}-{claim['id']}", 'title': claim.get('title') or '代码观察',
                'detail': claim['text'], 'basis': 'code', 'source': source, 'citations': claim['citations'],
            })
        if 'claims' not in report.report:
            for index, item in enumerate(report.report.get('architecture', [])):
                if item.get('citations') and item.get('detail'):
                    evidence.append({
                        'id': f'analysis-{report.id}-architecture-{index}', 'title': item['title'],
                        'detail': item['detail'], 'basis': 'unclassified', 'source': source, 'citations': item['citations'],
                    })
        for issue in report.report.get('issues', []):
            if issue.get('recommendation') and issue.get('title'):
                suggestions.append({
                    'id': f"analysis-{report.id}-issue-{issue['id']}", 'title': issue['title'],
                    'detail': issue['recommendation'],
                })
    return evidence, suggestions


@router.get('/projects/{project_id}/resume')
async def get_project_resume(project_id: int, ws: Workspace = Depends(get_workspace),
                             session: AsyncSession = Depends(get_session)):
    project = await _project(session, ws.id, project_id)
    draft = await session.get(ProjectResumeDraft, (ws.id, project_id))
    evidence, suggestions = await _repository_material(session, project)
    if not suggestions:
        suggestions = [{
            'id': 'contribution-details', 'title': '补充有依据的个人贡献',
            'detail': '写清你亲自完成的工作、为什么这样做，以及能验证的结果。',
        }]
    profile = project_profile(project)
    return {
        'project_id': project_id, 'evidence': evidence, 'suggestions': suggestions, 'draft': _draft_view(draft),
        'profile': {field: profile[field] for field in ('responsibilities', 'outcomes')},
    }


@router.put('/projects/{project_id}/resume')
async def save_project_resume(project_id: int, request: SaveResumeDraft, ws: Workspace = Depends(get_workspace),
                              session: AsyncSession = Depends(get_session)):
    if project_id != request.project_id:
        raise HTTPException(409, localized_message("errors.this_draft_belongs_to_a_different_project"))
    project = await _project(session, ws.id, project_id, lock=True)
    draft = await session.get(ProjectResumeDraft, (ws.id, project_id))
    current_revision = draft.revision if draft else 0
    if request.revision != current_revision:
        raise HTTPException(409, localized_message("errors.the_draft_has_changed_reopen_it_before_saving"))

    if draft is None:
        draft = ProjectResumeDraft(workspace_id=ws.id, project_id=project_id)
        session.add(draft)
    draft.revision = current_revision + 1
    draft.contribution = request.contribution
    draft.outcome = request.outcome
    parts = [project.name, f'本人职责：{request.contribution}']
    if request.outcome:
        parts.append(f'依据与结果：{request.outcome}')
    draft.content = '\n'.join(parts)
    draft.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return {'project_id': project_id, 'draft': _draft_view(draft)}
