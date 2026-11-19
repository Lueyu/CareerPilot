"""Postgres integration, controlled judge fault/response and real deterministic MCQ grading."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1 import exams, study_practice as api
from app.models import Evidence, Exam, ExamQuestion, Question, Skill, SkillState
from app.models.study_chat import StudySession, StudyMessage
from app.models.study_notes import StudyNote, StudyNoteVersion, StudyCard, StudyCardEvent
from app.services import assessment_jobs as jobs, assessment_worker as worker, study_practice as service
from app.services.assessment_rubric import AssessmentJudgeError
from app.services.learning_exposure import record_reference_exposure
from test_assessment_reliability import result_for


async def setup_lesson(session, ws, monkeypatch, *, with_card=False):
    async def grading_config(*args, **kwargs):
        return {'provider': 'fixture', 'model': 'controlled-judge', 'base_url': 'https://fixture.invalid',
                'api_key': 'not-a-live-key'}, {}, {}
    monkeypatch.setattr(jobs, 'runtime_config', grading_config)
    skill = Skill(key='study-' + uuid4().hex[:10], name='幂等测试域')
    session.add(skill)
    await session.flush()
    topic = '幂等键'
    def question(title, **changes):
        return Question(workspace_id=ws.id, short_code=uuid4().hex, qtype='mcq', skill=skill, topic=skill.key,
                        title=title, tags=[topic], options=['唯一约束', '直接重放'],
                        rubric={'answer_index': 0, 'max_points': 5}, **changes)
    source = question('10 个 K8s 副本中的幂等键如何判重？')
    session.add(source)
    await session.flush()
    exposed = question('用户看过的幂等键参考答案')
    fresh = question('异步支付重复通知时如何使用幂等键？')
    other = question('不同考点的无关题')
    other.tags = ['无关']
    discussed = question('上轮对话点名讲解过的幂等键题目')
    clone = question('２０个 Kubernetes 副本中的幂等键如何判重')
    session.add_all([question('原题同族变体', canonical_question_id=source.id), clone, exposed, other, discussed, fresh])
    await session.flush()
    await record_reference_exposure(session, ws, exposed, source='study-fixture')
    chat = StudySession(workspace_id=ws.id, title=topic,
                context={'topic': topic, 'skill_ids': [skill.id], 'question_id': source.id})
    session.add(chat)
    await session.flush()
    session.add_all([StudyMessage(session_id=chat.id, role=role, action='explain', request_id='first',
        status='completed', content=content) for role, content in [('user', '什么是幂等键？'),
            ('assistant', f'关于「{discussed.title}」，在原场景用唯一约束登记请求标识，重复时返回已有结果。')]])
    card = None
    if with_card:
        note = StudyNote(workspace_id=ws.id, origin_session_id=chat.id, title=topic)
        session.add(note)
        await session.flush()
        version = StudyNoteVersion(note_id=note.id, title=topic, body='原场景', author='user',
                                   session_ids=[chat.id], sources=[], coverage=[])
        session.add(version)
        await session.flush()
        card = StudyCard(workspace_id=ws.id, note_id=note.id, version_id=version.id,
                         question='怎样识别重放？', answer='以请求标识加唯一约束', knowledge_point=topic)
        session.add(card)
    await session.commit()
    return chat, skill, fresh, card


@pytest.mark.asyncio
@pytest.mark.parametrize('help_kind', [None, 'same_chat', 'separate_chat', 'card'])
async def test_study_practice_and_fresh_exam_share_grading_without_double_credit(
        db_session, test_workspace, monkeypatch, help_kind):
    db, ws = db_session, test_workspace
    chat, skill, fresh, card = await setup_lesson(db, ws, monkeypatch, with_card=True)
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)
    new = api.CreatePractice(kind='quiz', card_id=card.id, request_key='start')
    exercise = await api.create(chat.id, new, ws, db)
    assert exercise['question'] == card.question and card.answer not in str(exercise)
    pid = exercise['id']
    assert (await api.create(chat.id, new, ws, db))['id'] == pid
    with pytest.raises(HTTPException) as mismatch:
        await api.create(chat.id, new.model_copy(update={'kind': 'explain'}), ws, db)
    assert mismatch.value.status_code == 409
    first_req = api.Answer(text_answer='使用时间戳', request_key='answer-1')
    first = (await api.answer(pid, first_req, ws, db))['submissions'][0]
    assert not first['assistance']['hint_used'] and not first['assistance']['reference_viewed']
    assert (await api.answer(pid, first_req, ws, db))['submissions'][0]['id'] == first['id']
    hint = await api.assistance(pid, api.Assistance(kind='hint', request_key='hint'), ws, db)
    assert hint['assistance']['hint_used'] and hint['content'] != card.answer
    db.add(StudyCardEvent(card_id=card.id, kind='REVEAL', request_id='view-in-notebook'))
    await db.commit()
    second = await api.answer(pid, api.Answer(text_answer='原子记录请求标识并返回同一次结果。',
        request_key='answer-2', parent_submission_id=first['id']), ws, db)
    assert [s['payload']['text_answer'] for s in second['submissions']] == [
        first_req.text_answer, '原子记录请求标识并返回同一次结果。']
    assert second['submissions'][1]['assistance']['reference_viewed']
    assert second['submissions'][1]['assistance']['hint_used']
    assert second['submissions'][1]['assistance']['prior_attempt']
    assert not second['submissions'][0]['assistance']['reference_viewed']
    for grade in (0, 4):
        claim = await jobs.claim_step(db, 'fixture-worker')
        assert claim.payload['assessment_mode'] == 'PRACTICE'
        await jobs.complete_step(db, claim, result_for(claim, criterion_score=grade))
    await worker.reconcile(factory)
    assert await db.scalar(select(func.count(Evidence.id)).where(Evidence.workspace_id == ws.id)) == 0
    assert await db.scalar(select(SkillState.id).where(SkillState.workspace_id == ws.id)) is None
    detail = await api.detail(pid, ws, db)
    assert [s['feedback']['criterion_scores'][0]['score'] for s in detail['submissions']] == [0, 4]
    retest = await api.retest(pid, ws, db)
    assert retest['questions'][0]['short_code'] == fresh.short_code
    assert (await api.retest(pid, ws, db))['id'] == retest['id']
    with pytest.raises(HTTPException) as empty:
        await exams.submit_exam(retest['id'], ws, db, exams.SubmitExamReq(answers=[]))
    assert empty.value.status_code == 422
    await db.rollback()
    await db.refresh(ws)
    await db.refresh(skill)
    await db.refresh(chat)
    if help_kind == 'same_chat':
        await service.mark_chat_help(db, ws.id, chat.id)
        await db.commit()
    if help_kind == 'separate_chat':
        other_chat = StudySession(workspace_id=ws.id, title='另一次学习', context={})
        db.add(other_chat)
        await db.flush()
        await service.mark_chat_help(db, ws.id, other_chat.id, question_text='换个说法，重复来的两条通知我该怎么处理？')
        await db.commit()
    if help_kind == 'card':
        await db.refresh(card)
        db.add(StudyCardEvent(card_id=card.id, kind='REVEAL', request_id='during-exam'))
        await db.commit()
    payload = exams.SubmitExamReq(answers=[exams.SaveAnswerReq(order_no=1, choice_index=0)])
    await exams.submit_exam(retest['id'], ws, db, payload)
    claim = await jobs.claim_step(db, 'official-rule')
    assert claim.payload['assistance'].get('hint_used', False) == (help_kind in ('same_chat', 'separate_chat'))
    assert claim.payload['assistance']['reference_viewed'] == (help_kind == 'card')
    assert await jobs.complete_step(db, claim, worker.rule_result(claim.payload))
    await worker.reconcile(factory)
    again = await exams.submit_exam(retest['id'], ws, db, payload)
    result = await api.retest_status(pid, ws, db)
    assert again['score'] == 100 and result['status'] == 'COMPLETED'
    assert result['skill_key'] == skill.key
    assert len(result['observation_ids']) == 1
    assert len(result['evidence_ids']) == (0 if help_kind else 1)
    assert result['skill_state_before']['evidence_coverage'] == 0
    assert result['skill_state_after']['evidence_coverage'] == (0 if help_kind else 1)
    assert result['skill_state']['estimated_level'] is None
    eq = await db.scalar(select(ExamQuestion).where(ExamQuestion.exam_id == retest['id']))
    assert eq.frozen_snapshot['study_practice_id'] == pid
    assert eq.frozen_snapshot['submitted_answer']['choice_index'] == 0
    assert eq.frozen_snapshot['answer_revision_id'] and result['competency_snapshot_id']


@pytest.mark.asyncio
async def test_scoring_failure_in_practice_keeps_original_and_retry_without_evidence(db_session, test_workspace, monkeypatch):
    db, ws = db_session, test_workspace
    chat, _, _, _ = await setup_lesson(db, ws, monkeypatch)
    factory = async_sessionmaker(db.bind, expire_on_commit=False, autoflush=False)
    ex = await api.create(chat.id, api.CreatePractice(kind='explain', request_key='test'), ws, db)
    first = (await api.answer(ex['id'], api.Answer(text_answer='原答不覆盖', request_key='first'), ws, db))['submissions'][0]
    claim = await jobs.claim_step(db, 'fault-injection')
    await jobs.fail_step(db, claim, AssessmentJudgeError('AUTH_BLOCKED', 'controlled configuration failure'))
    await worker.reconcile(factory)
    observed = (await api.detail(ex['id'], ws, db))['submissions'][0]
    assert observed['status'] == 'FAILED' and observed['can_retry']
    assert observed['feedback'] is None and observed['payload']['text_answer'] == '原答不覆盖'
    assert await db.scalar(select(Evidence.id).where(Evidence.workspace_id == ws.id)) is None
    from app.api.v1.learning_feedback import retry
    await retry(first['id'], ws, db)
    claim = await jobs.claim_step(db, 'retry')
    await jobs.complete_step(db, claim, result_for(claim, criterion_score=0))
    await worker.reconcile(factory)
    observed = (await api.detail(ex['id'], ws, db))['submissions'][0]
    assert observed['id'] == first['id'] and observed['feedback']['criterion_scores'][0]['score'] == 0
    assert await db.scalar(select(Evidence.id).where(Evidence.workspace_id == ws.id)) is None
    foreign = SimpleNamespace(id=ws.id + 1000000)
    for op in (api.detail(ex['id'], foreign, db), api.history(chat.id, foreign, db),
               api.retest_status(ex['id'], foreign, db), api.answer(ex['id'],
                   api.Answer(text_answer='不能读取或修改', request_key='cross'), foreign, db)):
        with pytest.raises(HTTPException) as denied:
            await op
        assert denied.value.status_code == 404


def test_specific_concept_beats_shared_domain_tag():
    rag = SimpleNamespace(key='rag', name='检索增强')
    common = dict(skill=rag, tags=['RAG'], generation_meta={}, keywords='')
    broad = SimpleNamespace(id=1, title='如何流式输出 RAG 响应？', **common)
    narrow = SimpleNamespace(id=2, title='在新语料中如何设计混合检索与重排？', **common)
    assert list(service.matching_questions([broad, narrow], 'rag', 'RAG 中的重排')) == [2]
    assert not service.matching_questions([broad], 'rag', 'RAG 路由缓存')
    assert not service.matching_questions([broad], 'rag', 'RAG')


def test_retrieval_metric_matching_distinguishes_accuracy_and_generic_recall():
    rag = SimpleNamespace(key='rag', name='检索增强')
    common = dict(skill=rag, tags=['RAG', '检索'], generation_meta={}, keywords='')
    accuracy = SimpleNamespace(id=1, title='如何提升整体准确率和召回架构？', **common)
    architecture = SimpleNamespace(id=2, title='如何设计高并发召回架构？', **common)
    general_precision = SimpleNamespace(id=3, title='如何定义查准率？', **common)
    paired = SimpleNamespace(id=4, title='如何用检索条数计算查准率和查全率？', **common)
    tagged = SimpleNamespace(id=5, title='RAG 检索指标有哪些？', **{**common, 'keywords': 'precision recall'})
    scores = service.matching_questions([accuracy, architecture, general_precision, paired, tagged], 'rag',
        'RAG 中如何计算 Precision 和 Recall，分别使用哪些分母？')
    assert set(scores) == {4, 5}
    assert scores[4] > scores[5]


@pytest.mark.asyncio
async def test_card_uses_point_then_grounded_lesson_topic_and_refuses_contradictory_domains(db_session):
    async def skill(key, name):
        row = await db_session.scalar(select(Skill).where(Skill.key == key))
        if row is None:
            row = Skill(key=key, name=name)
            db_session.add(row)
            await db_session.flush()
        return row
    rag, evaluation = await skill('rag', '检索增强'), await skill('eval', '模型评估')
    ctx = {'topic': 'RAG 面试练习', 'skill_ids': []}
    assert (await service._resolve_skill(db_session, ctx, 'precision/recall', None)).id == rag.id
    assert (await service._resolve_skill(db_session, ctx, '查准和查全的权衡', None)).id == rag.id
    assert (await service._resolve_skill(db_session, ctx, '裁判一致性', None)).id == evaluation.id
    anchored = {**ctx, 'skill_ids': [rag.id]}
    with pytest.raises(HTTPException):
        await service._resolve_skill(db_session, anchored, '裁判一致性', evaluation.key)
    assert await service._resolve_skill(db_session, anchored, '裁判一致性', None) is None
    assert await service._resolve_skill(db_session, {}, '未归类的主题', None) is None
    assert await service._resolve_skill(db_session, {'topic': 'RAG 面试练习'}, '整理重点', None,
        card_evidence=('说说你的思路', '先列条件'), note_evidence=('学习整理', '记录步骤')) is None
    assert await service._resolve_skill(db_session, anchored, '信息检索评估指标', None,
        card_evidence=('Precision 和 Recall 在 RAG 检索计量中的分母分别是什么？', '相关结果和总共的相关文档')) == rag
    assert await service._resolve_skill(db_session, {}, '核对分母', None,
        card_evidence=('怎么做这个计算？', '使用分子除以分母'),
        note_evidence=('检索计量尺', 'RAG 中如何比较 Precision 和 Recall？')) == rag
    assert await service._resolve_skill(db_session, {'topic': 'RAG 面试练习'}, '复盘两个能力域', None,
        card_evidence=('比较 RAG 和 Eval 两个能力域', '分别检查'), note_evidence=('复盘', 'RAG')) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_card_retest_uses_frozen_precision_recall_prompt(db_session, test_workspace, monkeypatch, legacy):
    db, ws = db_session, test_workspace
    chat, _, _, card = await setup_lesson(db, ws, monkeypatch, with_card=True)
    rag = await db.scalar(select(Skill).where(Skill.key == 'rag'))
    if rag is None:
        rag = Skill(key='rag', name='检索增强')
        db.add(rag)
        await db.flush()
    chat.context = {'topic': 'RAG 检索计量尺', 'skill_ids': [rag.id]}
    version = await db.get(StudyNoteVersion, card.version_id)
    version.title = '检索计量尺'
    version.body = '在 RAG 检索中，Precision 和 Recall 使用不同分母。'
    card.question = '如果返回 5 条，其中 3 条相关，标注共有 6 条相关，Precision 和 Recall 分别是多少？如何解读？'
    card.answer = 'Precision = 3/5；Recall = 3/6。'
    card.knowledge_point = 'RAG 检索结果计算与应用'
    def question(title, tags):
        return Question(workspace_id=ws.id, short_code=uuid4().hex, title=title, skill=rag, topic=rag.key,
            qtype='mcq', options=['第一项', '第二项'], tags=tags, keywords=tags,
            rubric={'answer_index': 0, 'max_points': 5})
    broad = question('设计日均100万查询的企业级RAG系统', ['RAG', '检索', '高并发'])
    fresh = question('报销手册返回4条，2条相关，标注共有5条相关。计算查准率和查全率并解释分母。',
        ['RAG', '检索计量尺', 'precision', 'recall'])
    db.add_all([broad, fresh])
    await db.commit()
    exercise = await api.create(chat.id, api.CreatePractice(kind='quiz', card_id=card.id, request_key='start-rag'), ws, db)
    assert exercise['skill_key'] == 'rag'
    if legacy:
        row = await service.owned_practice(db, ws.id, exercise['id'])
        row.skill_id = None
        row.source = {key: value for key, value in row.source.items() if key != 'retest_topic'}
        from app.models.learning_feedback import LearningActivity
        activity = await db.get(LearningActivity, row.id)
        origin = {key: value for key, value in activity.snapshot['study_source'].items() if key != 'retest_topic'}
        assessment = {**activity.snapshot['assessment_question'], 'skill_id': None}
        activity.snapshot = {**activity.snapshot, 'study_source': origin, 'assessment_question': assessment}
    card.question = broad.title
    card.answer = '讨论吞吐量和缓存。'
    await db.commit()
    await api.answer(exercise['id'], api.Answer(text_answer='先分别计算两个分母。', request_key='submit-rag'), ws, db)
    issued = await api.retest(exercise['id'], ws, db)
    assert issued['questions'][0]['short_code'] == fresh.short_code
    assert (await api.detail(exercise['id'], ws, db))['skill_key'] == 'rag'


@pytest.mark.asyncio
@pytest.mark.parametrize('closed', ['ABORTED', 'EXPIRED'])
async def test_closed_retest_is_replaced_without_losing_or_reissuing_the_old_paper(
        db_session, test_workspace, monkeypatch, closed):
    db, ws = db_session, test_workspace
    chat, skill, fresh, _ = await setup_lesson(db, ws, monkeypatch)
    alternate = Question(workspace_id=ws.id, short_code=uuid4().hex, title='迁移批量写入时怎样沿用幂等键？',
        qtype='mcq', topic=skill.key, skill=skill, tags=['幂等键'], options=['按操作标识唯一', '忽略冲突'],
        rubric={'max_points': 5, 'answer_index': 0})
    db.add(alternate)
    await db.commit()
    pid = (await api.create(chat.id, api.CreatePractice(kind='quiz', request_key='new'), ws, db))['id']
    await api.answer(pid, api.Answer(text_answer='我会使用唯一约束。', request_key='submit'), ws, db)
    pending = StudyMessage(session_id=chat.id, request_id='explain-while-starting', role='assistant',
        status='generating', content='', action='explain', expires_at=datetime.now(timezone.utc) + timedelta(minutes=2))
    db.add(pending)
    await db.commit()
    with pytest.raises(HTTPException) as blocked:
        await api.retest(pid, ws, db)
    assert blocked.value.status_code == 409
    pending.status = 'interrupted'
    await db.commit()
    issued = await api.retest(pid, ws, db)
    if closed == 'ABORTED':
        await exams.abort_exam(issued['id'], ws, db)
    else:
        exam = await db.get(Exam, issued['id'])
        exam.started_at = datetime.now(timezone.utc) - timedelta(days=1)
        await db.commit()
    assert (await api.retest_status(pid, ws, db))['status'] == closed
    replacement = await api.retest(pid, ws, db)
    assert replacement['id'] != issued['id']
    assert issued['questions'][0]['short_code'] == fresh.short_code
    assert replacement['questions'][0]['short_code'] == alternate.short_code
    assert (await api.retest(pid, ws, db))['id'] == replacement['id']
    assert (await api.retest_status(pid, ws, db))['closed_retests'] == [{'exam_id': issued['id'], 'status': closed}]
