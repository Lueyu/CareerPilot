from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_workspace
from app.api.v1.project_autopilot import latest_run, owned_project, report_summary, run_view, valid_report, worker_view
from app.db import get_session
from app.models import ImportItem, ImportSession, Question, QuestionGeneration, Workspace
from app.services.project_memory import source_excerpts

router = APIRouter()


async def _bank_questions(session: AsyncSession, workspace_id: int, project_id: int) -> list[dict]:
    item = ImportItem.generation_meta['item']
    target_snapshot = QuestionGeneration.input_snapshot['project_issue_targets']
    # Approval creates a bank question or explicitly binds an existing one.
    rows = (await session.execute(select(
        Question.id, Question.short_code, Question.title, Question.source_type,
        QuestionGeneration.input_snapshot['source_analysis_id'], target_snapshot,
        item['fact_refs'],
    ).select_from(ImportItem)
        .join(ImportSession, ImportSession.id == ImportItem.session_id)
        .join(QuestionGeneration, QuestionGeneration.import_session_id == ImportSession.id)
        .join(Question, Question.id == func.coalesce(ImportItem.question_id, ImportItem.merged_into))
        .where(
            ImportSession.workspace_id == workspace_id,
            QuestionGeneration.workspace_id == workspace_id,
            (Question.workspace_id.is_(None)) | (Question.workspace_id == workspace_id),
            Question.generation_meta['assessment_use'].as_string().is_distinct_from('PRACTICE_ONLY'),
            ImportItem.status.in_(('APPROVED', 'MERGED')),
            item['project_id'].as_integer() == project_id,
            item['question_mode'].as_string() == 'project',
        ).order_by(QuestionGeneration.id.desc(), ImportItem.id.desc()))).all()
    questions = {}
    for question_id, short_code, title, source_type, analysis_id, targets, refs in rows:
        target = next((target for target in targets or [] if
                       target.get('project_id') == project_id and
                       target.get('analysis_id') == analysis_id and target.get('fact_ref') in (refs or [])), None)
        questions.setdefault(question_id, {
            'id': question_id, 'short_code': short_code, 'title': title, 'source_type': source_type,
            'analysis_id': analysis_id if target else None,
            'source_revision': target.get('source_revision') if target else None,
        })
    return list(questions.values())


async def _practice_questions(session: AsyncSession, workspace_id: int, project_id: int) -> list[dict]:
    interview_sets = ImportSession.meta['interview_sets']
    imports = (await session.execute(select(ImportSession.id, interview_sets).where(
        ImportSession.workspace_id == workspace_id,
        interview_sets.contains([{'project_id': project_id}]),
    ).order_by(ImportSession.id.desc()))).all()
    membership = {}
    for import_id, entries in imports:
        for entry in reversed(entries):
            if entry.get('project_id') != project_id or entry.get('import_id') != import_id:
                continue
            for question in entry.get('questions') or []:
                source_item_id = question.get('source_item_id')
                if source_item_id in (entry.get('item_ids') or []):
                    membership[(question['id'], entry['id'], source_item_id)] = (import_id, entry['title'])
    if not membership:
        return []

    meta = Question.generation_meta
    rows = (await session.execute(select(
        Question.id, Question.short_code, Question.title, Question.source_type, meta, ImportItem.session_id,
    ).select_from(Question).join(ImportItem, ImportItem.id == meta['source_item_id'].as_integer()).where(
        Question.workspace_id == workspace_id,
        Question.id.in_({key[0] for key in membership}),
        meta['assessment_use'].as_string() == 'PRACTICE_ONLY',
    ))).all()
    questions = {}
    for question_id, short_code, title, source_type, provenance, import_id in rows:
        set_id = provenance.get('interview_set_id')
        source_item_id = provenance.get('source_item_id')
        key = (question_id, set_id, source_item_id)
        linked = membership.get(key)
        if linked is None or linked[0] != import_id:
            continue
        questions[key] = {
            'id': question_id, 'short_code': short_code, 'title': title, 'source_type': source_type,
            'set_id': set_id, 'set_title': linked[1], 'import_id': import_id,
            'source_item_id': source_item_id, 'original_title': provenance.get('original_title'),
            'rewrite_method': provenance.get('rewrite_method'),
            'source_revision': (provenance.get('evidence') or {}).get('revision'),
        }
    return [questions[key] for key in membership if key in questions]


def _report_material(row) -> dict:
    technologies, recommendations, questions, limitations = [], [], [], []
    if row is None:
        return {'technologies': technologies, 'recommendations': recommendations, 'suggested_questions': questions,
                'limitations': limitations, 'source_excerpts': []}

    report = row.report
    limitations = report.get('unknowns') or []
    names = set()
    for claim in report.get('claims') or []:
        tech = claim.get('technology')
        if claim.get('kind') != 'CODE_OBSERVED' or not claim.get('citations') or not tech:
            continue
        name = tech['name']
        if name.casefold() in names:
            continue
        names.add(name.casefold())
        technologies.append({
            'name': name, 'use': claim['text'], 'limitations': tech['limitations'],
            'citations': claim['citations'], 'claim_id': claim['id'],
        })

    for issue in report.get('issues') or []:
        if not issue.get('citations') or not issue.get('recommendation'):
            continue
        recommendations.append({
            'id': issue['id'], 'title': issue['title'], 'detail': issue['recommendation'],
            'impact': issue.get('impact'), 'status': issue.get('status'), 'citations': issue['citations'],
        })
        for index, title in enumerate(issue.get('interview_questions') or []):
            questions.append({
                'id': f"{issue['id']}-{index + 1}", 'title': title, 'issue_id': issue['id'],
                'citations': issue['citations'],
            })

    references = {}
    for entry in technologies + recommendations:
        for ref in entry['citations']:
            key = (ref.get('source_id') or ref['path'], ref.get('source_version'), ref['start_line'], ref['end_line'])
            references[key] = ref
    excerpts = [item for item in source_excerpts(row, references.values()) if item['excerpt']]
    return {'technologies': technologies, 'recommendations': recommendations, 'suggested_questions': questions,
            'limitations': limitations, 'source_excerpts': excerpts}


@router.get('/projects/{project_id}/dossier')
async def get_project_dossier(project_id: int, ws: Workspace = Depends(get_workspace),
                              session: AsyncSession = Depends(get_session)):
    project = await owned_project(session, ws.id, project_id)
    row = await valid_report(session, project)
    run = await latest_run(session, project)
    return {
        'project_id': project.id, 'report': report_summary(project, row),
        'technologies_assessed': bool(row and row.report.get('technologies_assessed')),
        **_report_material(row),
        'latest_run': await run_view(session, run) if run else None,
        'worker': await worker_view(session),
        'bank_questions': await _bank_questions(session, ws.id, project.id),
        'practice_questions': await _practice_questions(session, ws.id, project.id),
    }
