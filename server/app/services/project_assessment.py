"""Freeze the exact reviewed project-question sources when an exam is created."""
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from app.i18n import message as localized_message
from app.models import ImportItem, QuestionGeneration


async def freeze_assessment(session, workspace_id, questions, generation_id=None):
    if generation_id is not None:
        task = await session.scalar(select(QuestionGeneration).where(
            QuestionGeneration.id == generation_id, QuestionGeneration.workspace_id == workspace_id))
        if task is None or not task.input_snapshot.get('project_issue_targets'):
            raise HTTPException(422, localized_message("errors.the_project_assessment_batch_is_missing_or_has_no"))
        drafts = (await session.scalars(select(ImportItem).where(ImportItem.session_id == task.import_session_id,
            ImportItem.status.in_(('APPROVED', 'MERGED'))))).all() if task.import_session_id else []
        allowed = {d.question_id or d.merged_into for d in drafts}
        if not questions or any(q.id not in allowed for q in questions):
            raise HTTPException(422, localized_message("errors.select_assessment_questions_approved_and_saved_from_this_batch"))
        refs = {ref for d in drafts if (d.question_id or d.merged_into) in {q.id for q in questions}
                for ref in (d.generation_meta or {}).get('item', {}).get('fact_refs', [])}
        sources = [{'analysis_id': task.input_snapshot['source_analysis_id'], 'generation_id': task.id,
                    'question_ids': [q.id for q in questions],
                    'issue_targets': [t for t in task.input_snapshot['project_issue_targets'] if t['fact_ref'] in refs]}]
    else:
        sources = []
        for q in questions:
            meta = q.generation_meta or {}
            snapshot = meta.get('input_snapshot') or {}
            refs = (meta.get('item') or {}).get('fact_refs') or []
            targets = [t for t in snapshot.get('project_issue_targets') or [] if t['fact_ref'] in refs]
            if targets:
                sources.append({'analysis_id': snapshot['source_analysis_id'], 'generation_id': meta['task_id'],
                                'question_ids': [q.id], 'issue_targets': targets})
    return deepcopy({'sources': sources}) if sources else None
