"""Internal practice scaffolds must not become approved bank material."""
import json

import pytest
from sqlalchemy import select
from test_assessment_snapshot_review import fixture_question
from test_question_generation import item  # noqa: F401 -- pytest 从模块命名空间发现 fixture，此导入即注册

from app.api.v1 import bank, question_generation
from app.models import ImportItem, QuestionGeneration
from app.services.question_review import approve_generated


@pytest.mark.asyncio
async def test_bank_lists_and_facets_exclude_internal_practice(db_session, test_workspace):
    question, _ = await fixture_question(db_session, test_workspace)
    question.generation_meta = {'assessment_use':'PRACTICE_ONLY'}
    question.company = 'Private practice only fixture'
    question.topic = 'practice-only-fixture'
    await db_session.commit()
    result = await bank.list_bank(q=question.title, ws=test_workspace, session=db_session)
    assert result['total'] == 0 and result['items'] == []
    assert not any(row['name'] == question.company for row in result['facets']['companies'])
    assert not any(row['name'] == question.topic for row in result['facets']['topics'])


@pytest.mark.asyncio
async def test_generation_context_and_approval_do_not_merge_into_practice(db_session, test_workspace, item):  # noqa: F811 -- item 是 pytest fixture 参数，遮蔽同名导入是有意为之
    question, skill = await fixture_question(db_session, test_workspace)
    question.generation_meta = {'assessment_use':'PRACTICE_ONLY'}
    await db_session.commit()
    view = await question_generation.create_task(question_generation.GenerationIn(mode='external',
        skill_keys=[skill.key], count=1), test_workspace, db_session)
    assert view['input_snapshot']['source_questions'] == []
    candidate = {**item, 'title':question.title, 'skill_key':skill.key, 'source_question_id':None}
    task = await db_session.get(QuestionGeneration, view['id'])
    await question_generation.store_response(db_session, task, json.dumps({
        'schema_version':1,'batch_id':task.id,'items':[candidate]}, ensure_ascii=False))
    draft = await db_session.scalar(select(ImportItem).where(ImportItem.session_id == task.import_session_id))
    assert draft.is_duplicate_of is None
    result = await approve_generated(db_session, test_workspace, task.import_session_id)
    assert len(result['created']) == 1 and result['merged'] == []
