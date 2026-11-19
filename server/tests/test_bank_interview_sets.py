import io
import json
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import bank
from app.api.v1.exams import CreateExamReq, issue_exam
from app.models import Exam, ExamQuestion, Project, Question, Skill, Workspace


def screenshot(name, text):
    font_path = next((path for path in (
        Path('C:/Windows/Fonts/arial.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
    ) if path.exists()), None)
    if font_path is None:
        pytest.skip('No scalable font for the OCR screenshot fixture')
    image = Image.new('RGB', (1550, 210), 'white')
    ImageDraw.Draw(image).text((38, 56), text, font=ImageFont.truetype(str(font_path), 49), fill='black')
    output = io.BytesIO()
    image.save(output, 'PNG')
    output.seek(0)
    return UploadFile(filename=name, file=output)


@pytest.fixture
def isolated_import_sessions(db_session, monkeypatch):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(bank, 'SessionLocal', factory)


@pytest.mark.asyncio
async def test_long_import_without_model_keeps_late_question_and_sources(
    db_session, test_workspace, isolated_import_sessions,
):
    filler = 'Synthetic interview notes.\n' * 320
    title = 'How does a queue preserve insertion order?'
    pasted_text = f'{filler}Q: {title}\nA: Items leave in insertion order.'
    duplicate = f'{filler}Q: {title.lower().rstrip("?")}\nA: A queue uses FIFO.'
    uploaded = await bank.start_import_upload(
        files=[UploadFile(filename='notes.txt', file=io.BytesIO(duplicate.encode()))],
        pasted_text=pasted_text, company='', level='', ws=test_workspace, session=db_session,
    )
    for task in list(bank._BG_IMPORT_TASKS):
        await task
    review = await bank.import_status(uploaded['id'], test_workspace, db_session)
    assert review['status'] == 'REVIEW', review['meta'].get('error')
    assert [item['title'] for item in review['items']] == [title]
    item = review['items'][0]
    assert item['draft_answer'] == 'Items leave in insertion order.'
    assert item['generation_meta'] == {'source_files': ['粘贴文本', 'notes.txt'], 'extraction_method': 'rules'}


@pytest.mark.asyncio
async def test_long_import_keeps_late_question_when_model_returns_early_item(
    db_session, test_workspace, isolated_import_sessions, monkeypatch,
):
    early_title = 'How do workers claim a job?'
    late_title = 'How does a queue preserve insertion order?'
    pasted_text = (
        f'Q: {early_title}\nA: Use an atomic update.\n'
        + 'Synthetic interview notes.\n' * 320
        + f'Q: {late_title}\nA: Items leave in insertion order.'
    )
    prompts = []

    async def model_reply(self, system, user, **kwargs):
        prompts.append(user)
        return {'content': json.dumps({'questions': [{'title': early_title, 'draft_answer': 'Use an atomic update.'}]})}

    monkeypatch.setattr(bank.AgentAdapter, 'active', True)
    monkeypatch.setattr(bank.AgentAdapter, 'chat', model_reply)
    uploaded = await bank.start_import(bank.StartImportReq(pasted_text=pasted_text), test_workspace, db_session)
    for task in list(bank._BG_IMPORT_TASKS):
        await task
    review = await bank.import_status(uploaded['id'], test_workspace, db_session)
    assert review['status'] == 'REVIEW', review['meta'].get('error')
    assert [item['title'] for item in review['items']] == [early_title, late_title]
    assert [item['generation_meta'] for item in review['items']] == [
        {'source_files': ['粘贴文本'], 'extraction_method': 'model'},
        {'source_files': ['粘贴文本'], 'extraction_method': 'rules'},
    ]
    assert prompts == [pasted_text[:8000]]


@pytest.mark.asyncio
async def test_long_import_respects_text_and_item_caps(db_session, test_workspace, isolated_import_sessions):
    titles = [f'How does fixture queue {number} handle retries?' for number in range(21)]
    questions = '\n'.join(f'Q: {title}\nA: Use a retry budget.' for title in titles)
    notes = 'x' * (60_000 - 1 - len(questions)) + '\n' + questions
    duplicate = f'Q: {titles[0]}\nA: Use a retry budget.'
    uploaded = await bank.start_import_upload(
        files=[
            UploadFile(filename='long.txt', file=io.BytesIO(notes.encode())),
            UploadFile(filename='over-budget.txt', file=io.BytesIO(duplicate.encode())),
        ],
        pasted_text='', company='', level='', ws=test_workspace, session=db_session,
    )
    for task in list(bank._BG_IMPORT_TASKS):
        await task
    review = await bank.import_status(uploaded['id'], test_workspace, db_session)
    assert review['status'] == 'REVIEW', review['meta'].get('error')
    assert review['meta']['extracted_source_text'] == notes
    assert [item['title'] for item in review['items']] == titles[:20]
    assert all(item['generation_meta']['source_files'] == ['long.txt'] for item in review['items'])


@pytest.mark.asyncio
async def test_screenshots_to_project_paper_with_explicit_promotion(db_session, test_workspace, tmp_path, monkeypatch):
    pytest.importorskip('rapidocr_onnxruntime')
    from app.models.question import ImportSession
    # Existing installations can contain gaps after a restore or an earlier deletion.
    count = await db_session.scalar(select(func.count(ImportSession.id)))
    db_session.add(ImportSession(workspace_id=test_workspace.id, short_code=f'IM-{count + 102}',
                                 status='REVIEWED', meta={}))
    skill = await db_session.scalar(select(Skill).where(Skill.key == 'agent'))
    if skill is None:
        db_session.add(Skill(key='agent', name='Agent'))
    project = Project(workspace_id=test_workspace.id, name='Queue fixture', repo_url='fixture', owner='test', repo='queue')
    db_session.add(project)
    await db_session.commit()
    (tmp_path / 'queue.py').write_text('def save_item(value):\n    return value\n', encoding='utf-8')
    monkeypatch.setenv('STUDY_PROJECT_ROOTS', json.dumps({f'{test_workspace.id}:{project.id}': str(tmp_path)}))
    uploaded = await bank.start_import_upload(files=[
        screenshot('first.png', 'How does save_item work?'), screenshot('second.png', 'Why use a retry budget?'),
        UploadFile(filename='invalid.png', file=io.BytesIO(b'bad PNG bytes')),
    ], pasted_text='', company='Fixture bank', level='', ws=test_workspace, session=db_session)
    for task in list(bank._BG_IMPORT_TASKS):
        await task
    review = await bank.import_status(uploaded['id'], test_workspace, db_session)
    assert review['status'] == 'REVIEW', review['meta']
    assert [row['ok'] for row in review['meta']['files']] == [True, True, False]
    assert [item['generation_meta']['source_files'] for item in review['items']] == [['first.png'], ['second.png']]
    first, second = review['items']
    req = bank.InterviewSetRequest(project_id=project.id, item_ids=[first['id'], second['id']],
        title='Queue practice fixture', request_id='create-fixture', edits={first['id']: 'How does save_item preserve the input?'})
    entry = await bank.create_interview_set(review['id'], req, test_workspace, db_session)
    again = await bank.create_interview_set(review['id'], req, test_workspace, db_session)
    assert entry['id'] == again['id']
    assert entry['questions'][0]['evidence']['path'] == 'queue.py'
    assert entry['questions'][0]['extracted_title'] == first['title']
    assert entry['questions'][1]['evidence'] is None
    assert entry['questions'][1]['rewrite_method'] == 'conditional_rules'
    assert first['generation_meta']['extraction_method'] == 'rules'
    from app.services import interview_rewrite
    manual = Project(workspace_id=test_workspace.id, name='Manual queue', repo_url='', owner='', repo='',
                     meta={'kind': 'manual', 'profile': {'role': 'Backend engineer', 'responsibilities': 'Owned idempotency and duplicate suppression'}})
    db_session.add(manual)
    await db_session.commit()
    async def configured():
        return {'provider': 'fixture', 'base_url': 'fixture', 'api_key': 'fixture'}

    async def model_reply(self, cfg, system, user, **kwargs):
        payload = json.loads(user)
        ref = f'P{manual.id}-responsibilities'
        assert payload['facts'][ref]['provenance'] == 'user_statement'
        assert payload['facts'][ref]['text'] == 'Owned idempotency and duplicate suppression'
        assert [item['item_id'] for item in payload['items']] == [first['id'], second['id']]
        assert cfg['output_limit'] == 4096
        return {'content': json.dumps({'questions': [
            {'item_id': first['id'], 'question': '你如何验证重复请求不会写入第二条队列记录？', 'fact_refs': [ref]},
            {'item_id': second['id'], 'question': '你如何验证重试不会超过最大预算？', 'fact_refs': ['other-project']},
        ]})}

    with monkeypatch.context() as patch:
        patch.setattr(interview_rewrite, 'get_llm_config', configured)
        patch.setattr(interview_rewrite.AgentAdapter, 'chat_with', model_reply)
        result = await bank.create_interview_set(review['id'], req.model_copy(update={'project_id': manual.id, 'request_id': 'manual-fixture'}), test_workspace, db_session)
    assert result['questions'][0]['title'] == '你如何验证重复请求不会写入第二条队列记录？'
    assert result['questions'][0]['fact_refs'][0]['ref'] == f'P{manual.id}-responsibilities'
    assert result['questions'][1]['rewrite_method'] == 'conditional_rules'
    assert result['questions'][1]['rewrite_fallback_reason'] == 'model_no_grounded_question'
    assert await bank.list_interview_sets(Workspace(id=-1), db_session) == {'items': []}
    with pytest.raises(HTTPException) as forbidden:
        await bank.create_interview_paper(review['id'], entry['id'], bank.InterviewPaperRequest(request_id='other'), Workspace(id=-1), db_session)
    assert forbidden.value.status_code == 404
    with pytest.raises(HTTPException) as generic:
        await issue_exam(CreateExamReq(kind=Exam.KIND_BANK, question_ids=[entry['questions'][0]['id']]), test_workspace, db_session)
    assert generic.value.status_code == 409
    await db_session.rollback()
    await db_session.refresh(test_workspace)
    before = await bank.list_bank(company='Fixture bank', ws=test_workspace, session=db_session)
    assert before['total'] == 0
    paper_req = bank.InterviewPaperRequest(request_id='paper-fixture')
    paper = await bank.create_interview_paper(review['id'], entry['id'], paper_req, test_workspace, db_session)
    paper_retry = await bank.create_interview_paper(review['id'], entry['id'], paper_req, test_workspace, db_session)
    assert paper_retry['id'] == paper['id'] and paper['title'] == entry['title']
    frozen = (await db_session.scalars(select(ExamQuestion).where(ExamQuestion.exam_id == paper['id']).order_by(ExamQuestion.order_no))).all()
    assert [row.question_id for row in frozen] == [question['id'] for question in entry['questions']]
    assert frozen[0].creation_snapshot['interview_set']['evidence']['path'] == 'queue.py'
    promoted = await bank.approve_import(review['id'], bank.ApproveItemReq(item_ids=[first['id']]), test_workspace, db_session)
    after = await bank.list_bank(company='Fixture bank', ws=test_workspace, session=db_session)
    assert len(promoted['created']) == after['total'] == 1
    assert after['items'][0]['title'] == first['title']


@pytest.mark.asyncio
async def test_bank_facets_intersect_and_keep_stable_pages(db_session, test_workspace):
    for suffix, company, topic in [('one', 'Fixture A', 'rag'), ('two', 'Fixture B', 'rag'),
                                   ('three', 'Fixture A', 'harness'), ('four', 'Fixture C', 'rag')]:
        db_session.add(Question(workspace_id=test_workspace.id, short_code=f'BANK-{suffix}-{test_workspace.id}',
            title=f'Facet fixture {suffix}', company=company, topic=topic, source_type='USER_PRIVATE'))
    await db_session.commit()
    filters = dict(companies=['Fixture A', 'Fixture B'], topics=['rag'], sources=['USER_PRIVATE'], q='Facet fixture',
                   ws=test_workspace, session=db_session, limit=1)
    first = await bank.list_bank(**filters)
    second = await bank.list_bank(**filters, offset=1)
    assert first['total'] == second['total'] == 2
    assert first['items'][0]['id'] < second['items'][0]['id']
    assert {row['name']: row['count'] for row in first['facets']['topics']} == {'rag': 2, 'harness': 1}
    assert {row['name']: row['count'] for row in first['facets']['companies']} == {'Fixture A': 1, 'Fixture B': 1, 'Fixture C': 1}
