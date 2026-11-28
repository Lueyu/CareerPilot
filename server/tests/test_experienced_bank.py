"""Install and grade experienced-bank practice in an isolated test schema."""
import asyncio
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema
from test_assessment_reliability import result_for

from app import seed
from app.api.deps import require_auth
from app.api.v1 import bank, exams
from app.db import get_session
from app.models import (
    CompetencySnapshot,
    Event,
    Evidence,
    Exam,
    ExamQuestion,
    LearningTask,
    Question,
    ScoringRun,
    Skill,
    Workspace,
)
from app.services import answer_guides, assessment_jobs, assessment_worker, experienced_bank
from app.services.answer_guides import for_question, published_guides
from app.services.learning_exposure import EVENT_KIND
from app.services.local_credentials import AuthenticatedPrincipal
from app.services.recovery_control import provision_known_installation

CRITERIA = ["说明连接超时的可观测信号", "给出重试预算与退避策略", "说明如何检查重复请求的副作用"]


def test_shipped_domains_cover_both_experience_levels():
    domains = experienced_bank.load_catalog()
    keys = [domain.key for domain in domains]
    assert keys == ["engineering", "models", "context", "tools", "rag", "orchestration",
                    "production", "evaluation", "governance", "delivery", "training"]
    skills = {row[0] for row in seed.SKILLS}
    for domain in domains:
        for level in ("mid", "senior"):
            assert sum(q.level == level for q in domain.questions) >= 3, domain.key
        assert all(q.skill in skills for q in domain.questions)


def test_tutorials_are_bound_to_the_published_question():
    authored = {q.code for domain in experienced_bank.load_catalog() for q in domain.questions}
    originals = experienced_bank.original_identities()
    guides = published_guides()
    assert authored <= guides.keys() <= authored | originals.keys()
    assert answer_guides.beginner_examples().keys() == originals.keys()
    assert {g.project.name for g in guides.values()} == {"AIDR", "Squabble", "CareerPilot"}
    code = "rag-001"
    title, topic = originals[code]
    row = Question(short_code=code, title=title, topic=topic, qtype="open",
                   source_type="PUBLIC_LICENSED", is_demo=False)
    assert for_question(row) == guides[code]
    assert answer_guides.example_for(row)
    row.workspace_id = 7
    assert for_question(row) is None
    assert answer_guides.example_for(row) is None
    row.workspace_id = None
    row.title = "An unrelated question"
    assert for_question(row) is None


def test_reference_corrections_only_apply_to_unedited_shared_originals(monkeypatch):
    from hashlib import sha256

    code = "rag-001"
    old_html = "<p>Original published reference</p>"
    corrected = json.loads(seed.BANK_PATH.read_text(encoding="utf-8"))["questions"]
    expected_html = next(item["html"] for item in corrected if item["id"] == code)
    monkeypatch.setitem(answer_guides.PREVIOUS_REFERENCE_DIGESTS, code, sha256(old_html.encode()).hexdigest())
    title, topic = experienced_bank.original_identities()[code]
    row = Question(short_code=code, title=title, topic=topic, qtype="open",
                   source_type="PUBLIC_LICENSED", is_demo=False, answer_html=old_html)
    assert answer_guides.current_reference(row) == expected_html
    row.workspace_id = 5
    assert answer_guides.current_reference(row) == old_html
    row.workspace_id = None
    row.answer_html = "<p>Locally edited reference</p>"
    assert answer_guides.current_reference(row) == row.answer_html


def test_authored_reference_update_can_run_without_optional_frontend_bank(tmp_path, monkeypatch):
    code = "xp-production-03"
    domain, authored = next((d, q) for d in experienced_bank.load_catalog() for q in d.questions if q.code == code)
    row = Question(short_code=code, title=authored.title, qtype="open", source_type="PUBLIC_LICENSED",
                   is_demo=False, answer_html="<p>Prior authored reference</p>", generation_meta={
                       "pack": experienced_bank.PACK, "origin": experienced_bank.ORIGIN,
                       "assessment_use": experienced_bank.PRACTICE_ONLY, "domain_key": domain.key,
                   })
    from hashlib import sha256
    monkeypatch.setitem(answer_guides.PREVIOUS_REFERENCE_DIGESTS, code, sha256(row.answer_html.encode()).hexdigest())
    monkeypatch.setattr(seed, "BANK_PATH", tmp_path / "absent.json")
    answer_guides._corrected_references.cache_clear()
    try:
        assert answer_guides.current_reference(row) == experienced_bank.render_answer_html(authored)
    finally:
        answer_guides._corrected_references.cache_clear()


def test_shipped_legacy_mapping_covers_each_original_once():
    mapping = json.loads(experienced_bank.LEGACY_DOMAINS_PATH.read_text(encoding="utf-8"))
    originals = json.loads(seed.BANK_PATH.read_text(encoding="utf-8"))["questions"]
    assert set(mapping) == {domain.key for domain in experienced_bank.load_catalog()}
    assert all(set(levels) == {"mid", "senior"} for levels in mapping.values())
    assert len(originals) == 161
    assert Counter(
        code for levels in mapping.values() for codes in levels.values() for code in codes
    ) == {entry["id"]: 1 for entry in originals}
    assert len({entry["id"] for entry in originals}) == len(originals)


def authored(code, level="mid"):
    return {
        "code": code, "level": level, "title": f"怎样调试有副作用的请求超时？（{code}）",
        "skill": "rag", "tags": ["超时", "重试"], "answer": "原始参考 <script>alert(1)</script>\n第二段",
        "followups": [
            {"question": "追问一 <img>", "answer": "回答一 & 检查"},
            {"question": "追问二", "answer": "回答二"},
        ],
        "criteria": CRITERIA,
    }


def write_domain(root, filename, key, order, questions):
    path = root / filename
    path.write_text(json.dumps({
        "key": key, "name": "可靠性" if order == 1 else "交付", "order": order,
        "focus": "检查失败时的行为", "questions": questions,
    }, ensure_ascii=False), encoding="utf-8")


@pytest_asyncio.fixture
async def isolated_seed(db_session, monkeypatch, tmp_path):
    schema = "experienced_" + uuid4().hex
    async with db_session.bind.begin() as conn:
        await conn.execute(CreateSchema(schema))
    engine = create_async_engine(db_session.bind.url, poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with engine.begin() as conn:
        migration = Path(__file__).resolve().parents[1] / "migrations/20261128_recovery_control.sql"
        await conn.exec_driver_sql(migration.read_text(encoding="utf-8"))
    async with factory.begin() as session:
        await provision_known_installation(session)
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"questions": [
        {"id": "LEGACY-Q1", "topic": "rag", "title": "旧题原文", "html": "<p>旧题解析</p>"},
    ]}), encoding="utf-8")
    legacy_domains = tmp_path / "legacy_domains.json"
    legacy_domains.write_text(json.dumps({"reliability": {"mid": [], "senior": ["LEGACY-Q1"]}}))
    monkeypatch.setattr(experienced_bank, "LEGACY_DOMAINS_PATH", legacy_domains)
    monkeypatch.setattr(seed, "engine", engine)
    monkeypatch.setattr(seed, "SessionLocal", factory)
    monkeypatch.setattr(seed, "BANK_PATH", legacy)
    monkeypatch.setattr(experienced_bank, "CATALOG_PATH", catalog)
    try:
        yield factory, catalog
    finally:
        await engine.dispose()
        async with db_session.bind.begin() as conn:
            await conn.execute(DropSchema(schema, cascade=True))


async def by_code(session, code):
    return await session.scalar(select(Question).where(Question.short_code == code))


def other_question(code, skill_id, *, workspace_id=None, meta=None):
    return Question(short_code=code, title=f"既有题 {code}", topic="rag", skill_id=skill_id,
        workspace_id=workspace_id, generation_meta=meta, answer_html="<p>既有解析</p>",
        rubric={"points": [{"title": "原评分项"}]})


@pytest.mark.asyncio
async def test_seed_concurrent_fresh_and_reseed_with_collisions(isolated_seed, capsys):
    factory, catalog = isolated_seed
    write_domain(catalog, "first.json", "reliability", 1, [authored("EXP-Z")])

    assert [domain.key for domain in experienced_bank.load_catalog()] == ["reliability"]
    await asyncio.gather(seed.seed(), seed.seed())
    output = capsys.readouterr().out
    assert "1 created, 0 already installed, 0 collisions" in output
    assert "0 created, 1 already installed, 0 collisions" in output

    async with factory() as session:
        workspace = await session.scalar(select(Workspace).where(Workspace.slug == "personal"))
        initial = await by_code(session, "EXP-Z")
        legacy = await by_code(session, "LEGACY-Q1")
        skill = await session.scalar(select(Skill).where(Skill.key == "rag"))
        assert initial is not None and legacy is not None and initial.skill_id == legacy.skill_id == skill.id
        assert initial.source_type == "PUBLIC_LICENSED" and initial.workspace_id is None
        assert initial.topic == "可靠性" and initial.level == "mid" and initial.qtype == "open"
        original_ids = (initial.id, legacy.id)
        initial.answer_html = "<p>Previously installed pack reference</p>"
        session.add_all([
            other_question("EXP-PRIVATE", skill.id, workspace_id=workspace.id, meta=initial.generation_meta),
            other_question("EXP-UNRELATED", skill.id),
            other_question("EXP-WRONG-DOMAIN", skill.id,
                meta={**initial.generation_meta, "domain_key": "some_other_domain"}),
        ])
        await session.commit()

    write_domain(catalog, "first.json", "reliability", 1, [
        authored("EXP-Z"), authored("EXP-B", "senior"), authored("EXP-PRIVATE"),
        authored("EXP-UNRELATED"), authored("EXP-WRONG-DOMAIN"),
    ])
    await seed.seed()
    output = capsys.readouterr().out
    assert "1 created, 1 already installed, 3 collisions" in output
    for code in ("EXP-PRIVATE", "EXP-UNRELATED", "EXP-WRONG-DOMAIN"):
        assert f"short_code collision: {code!r}" in output
    async with factory() as session:
        initial = await by_code(session, "EXP-Z")
        legacy = await by_code(session, "LEGACY-Q1")
        assert (initial.id, legacy.id) == original_ids
        assert initial.answer_html == "<p>Previously installed pack reference</p>"
        assert legacy.answer_html == "<p>旧题解析</p>"
        assert (await by_code(session, "EXP-B")).level == "senior"
        assert (await by_code(session, "EXP-PRIVATE")).workspace_id == workspace.id
        assert (await by_code(session, "EXP-UNRELATED")).answer_html == "<p>既有解析</p>"
        assert (await by_code(session, "EXP-WRONG-DOMAIN")).generation_meta["domain_key"] == "some_other_domain"
        listing = await experienced_bank.list_installed_pack(session)
        assert [row["short_code"] for row in listing["domains"][0]["questions"]] == ["EXP-Z", "EXP-B", "LEGACY-Q1"]


@pytest.mark.asyncio
async def test_legacy_install_after_pack_and_resume_after_twenty_practices(isolated_seed, monkeypatch):
    factory, catalog = isolated_seed
    write_domain(catalog, "a.json", "tools", 1, [authored("EXP-ONE")])
    with monkeypatch.context() as mp:
        mp.setattr(seed, "BANK_PATH", catalog / "absent")
        await seed.seed()
    await seed.seed()

    async with factory() as session:
        workspace = await session.scalar(select(Workspace).where(Workspace.slug == "personal"))
        question = await by_code(session, "EXP-ONE")
        assert await by_code(session, "LEGACY-Q1") is not None
        active = Exam(workspace_id=workspace.id, title="既有诊断", kind="diagnosis", status="ACTIVE")
        session.add(active)
        await session.flush()
        for number in range(21):
            session.add(Exam(workspace_id=workspace.id, title=str(number), kind="focused", status="COMPLETED"))
            session.add(Exam(workspace_id=workspace.id, title=str(number), kind="practice", status="ACTIVE",
                questions=[ExamQuestion(order_no=1, question_id=question.id, creation_snapshot={"experienced_bank_practice": True})]))
        await session.commit()
        history = (await exams.list_exams(ws=workspace, session=session))["history"]
        assert active.id in {row["id"] for row in history}
        assert sum(row["kind"] == "focused" for row in history) == 20
        assert sum(row["experienced_bank_practice"] for row in history) == 20

        rows, cursor = [], None
        for size in (20, 20, 3):
            page = await exams.list_exam_history(before_id=cursor, ws=workspace, session=session)
            assert len(page["history"]) == size and page["total_count"] == 43
            rows.extend(page["history"])
            cursor = page["next_cursor"]
            assert cursor == (rows[-1]["id"] if size == 20 else None)
        ids = [row["id"] for row in rows]
        assert len(ids) == 43 and ids == sorted(set(ids), reverse=True)
        assert ids[-1] == active.id
        assert sum(row["experienced_bank_practice"] for row in rows) == 21


@pytest.mark.asyncio
async def test_catalog_listing_exposure_and_practice_scoring(isolated_seed, monkeypatch, tmp_path):
    factory, catalog = isolated_seed
    write_domain(catalog, "a.json", "delivery", 2, [authored("EXP-M", "senior")])
    write_domain(catalog, "z.json", "reliability", 1, [authored("EXP-Z"), authored("EXP-A")])
    await seed.seed()
    async with factory() as session:
        personal = await session.scalar(select(Workspace).where(Workspace.slug == "personal"))
        foreign = Workspace(slug="foreign", name="其他工作空间", is_demo=False)
        session.add(foreign)
        await session.flush()
        first = await by_code(session, "EXP-Z")
        second = await by_code(session, "EXP-A")
        second.company = "Fixture Company"
        third = await by_code(session, "EXP-M")
        legacy = await by_code(session, "LEGACY-Q1")
        foreign_q = other_question("EXP-FOREIGN", first.skill_id, workspace_id=foreign.id, meta=first.generation_meta)
        omitted = other_question("EXP-OMITTED", first.skill_id, meta=first.generation_meta)
        retest = LearningTask(workspace_id=personal.id, skill_id=first.skill_id, kind="RETEST", title="正式复测")
        private = other_question("PRIVATE-Q", first.skill_id, workspace_id=foreign.id)
        old_practice = Exam(workspace_id=personal.id, title="旧学习练习", kind="practice", status="ACTIVE")
        session.add_all([foreign_q, omitted, private, old_practice, retest])
        await session.commit()

    # The catalog references one foreign row and one uninstalled row.
    write_domain(catalog, "z.json", "reliability", 1, [
        authored("EXP-Z"), authored("EXP-A"), authored("EXP-FOREIGN"), authored("EXP-UNINSTALLED"),
    ])

    async def session_dependency():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(bank.router)
    app.include_router(exams.router)
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[require_auth] = lambda: AuthenticatedPrincipal("fixture", "fixture", "OWNER", None)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/bank/experienced")
        assert response.status_code == 200
        assert response.json() == {"domains": [
            {"key": "reliability", "name": "可靠性", "order": 1, "focus": "检查失败时的行为", "questions": [
                {"id": first.id, "short_code": "EXP-Z", "level": "mid", "title": first.title, "tags": ["超时", "重试"], "source": "authored", "company": None},
                {"id": second.id, "short_code": "EXP-A", "level": "mid", "title": second.title, "tags": ["超时", "重试"], "source": "authored", "company": "Fixture Company"},
                {"id": legacy.id, "short_code": "LEGACY-Q1", "level": "senior",
                 "title": legacy.title, "tags": [], "source": "legacy", "company": None},
            ]},
            {"key": "delivery", "name": "交付", "order": 2, "focus": "检查失败时的行为", "questions": [
                {"id": third.id, "short_code": "EXP-M", "level": "senior", "title": third.title, "tags": ["超时", "重试"], "source": "authored", "company": None},
            ]},
        ]}
        async with factory() as session:
            assert await session.scalar(select(func.count(Event.id)).where(Event.kind == EVENT_KIND)) == 0

        assert (await client.get("/bank/EXP-FOREIGN")).status_code == 404
        assert (await client.post("/exams", json={"kind": "bank_exam", "question_ids": [foreign_q.id]})).status_code == 404
        assert (await client.get("/bank/EXP-FOREIGN", headers={"X-Workspace": "foreign"})).status_code == 404
        assert (await client.get("/bank/PRIVATE-Q")).status_code == 404
        private_detail = await client.get("/bank/PRIVATE-Q", headers={"X-Workspace": "foreign"})
        assert private_detail.status_code == 200 and private_detail.json()["id"] == private.id
        assert (await client.post("/bank/questions/EXP-Z/answer?force=true")).status_code == 403
        for _ in range(2):
            detail = await client.get("/bank/EXP-Z")
            assert detail.status_code == 200
        html = detail.json()["answer_html"]
        assert "原始参考 &lt;script&gt;alert(1)&lt;/script&gt;<br>第二段" in html
        assert "追问一 &lt;img&gt;" in html and "回答一 &amp; 检查" in html and "回答二" in html
        assert all(criterion in html for criterion in CRITERIA)
        assert "<script>" not in html and "<img>" not in html

        automatic_retest = await client.post("/exams", json={"kind": "focused", "retest_task_id": retest.id})
        assert automatic_retest.status_code == 200, automatic_retest.text
        assert [q["short_code"] for q in automatic_retest.json()["questions"]] == ["LEGACY-Q1"]
        diagnosis = await client.post("/exams", json={"kind": "diagnosis", "question_count": 20})
        assert [q["short_code"] for q in diagnosis.json()["questions"]] == ["LEGACY-Q1"]
        assert (await client.post("/exams", json={"kind": "focused", "question_ids": [first.id]})).status_code == 422
        mixed = await client.post("/exams", json={"kind": "bank_exam", "question_ids": [first.id, legacy.id]})
        assert mixed.status_code == 422

        ordinary = await client.post("/exams", json={"kind": "bank_exam", "question_ids": [legacy.id],
                                                     "idempotency_key": "ordinary"})
        assert ordinary.status_code == 200 and ordinary.json()["kind"] == "bank_exam"
        assert ordinary.json()["experienced_bank_practice"] is False
        assert (await client.post("/exams", json={"kind": "bank_exam", "question_ids": [first.id]})).json()[
            "experienced_bank_practice"] is True

        request = {"kind": "bank_exam", "experienced_bank": True, "question_ids": [first.id, third.id, legacy.id]}
        for invalid in (
            {"kind": "diagnosis"}, {"question_ids": []}, {"retest_task_id": retest.id},
            {"project_generation_id": 1}, {"question_ids": [legacy.id, private.id]},
            {"question_ids": [legacy.id, omitted.id]}, {"question_ids": [-1]},
        ):
            assert (await client.post("/exams", json={**request, **invalid})).status_code == 422
        assert (await client.post("/exams", json={**request, "question_ids": [legacy.id],
                                                 "idempotency_key": "ordinary"})).status_code == 409
        async with factory() as session:
            with pytest.raises(HTTPException) as blocked:
                await exams.issue_exam(exams.CreateExamReq(**request), personal, session,
                                       interview_set={"id": "fixture"})
            assert blocked.value.status_code == 422
        response = await client.post("/exams", json=request)
        assert response.status_code == 200, response.text
        practice = response.json()
        assert practice["kind"] == "practice" and practice["experienced_bank_practice"] is True
        assert practice["title"] == "题库练习"
        assert [row["short_code"] for row in practice["questions"]] == ["EXP-Z", "EXP-M", "LEGACY-Q1"]
        assert "原始参考" not in response.text and all(c not in response.text for c in CRITERIA)
        url = f"/exams/{practice['id']}"
        assert (await client.get(url, headers={"X-Workspace": "foreign"})).status_code == 404
        for history_path in ("/exams", "/exams/history"):
            history = (await client.get(history_path)).raise_for_status().json()["history"]
            resume = next(row for row in history if row["id"] == practice["id"])
            assert resume["status"] == "ACTIVE" and resume["experienced_bank_practice"] is True
            assert old_practice.id not in {row["id"] for row in history}
            other = await client.get(history_path, headers={"X-Workspace": "foreign"})
            assert other.raise_for_status().json()["history"] == []
        assert (await client.get(url)).json()["experienced_bank_practice"] is True
        assert (await client.get(f"/exams/{old_practice.id}")).json()["experienced_bank_practice"] is False

        config = {"provider": "fixture", "model": "judge-fixture",
                  "base_url": "https://fixture.invalid", "api_key": "fixture"}
        async def runtime_config(*args, **kwargs):
            return config, {}, {}

        received = []
        async def judge(_adapter, **kwargs):
            received.append(kwargs)
            return result_for(SimpleNamespace(payload={**kwargs, "submitted_answer": {"text_answer": kwargs["text"]}}))

        monkeypatch.setattr(assessment_jobs, "runtime_config", runtime_config)
        monkeypatch.setattr(assessment_worker, "runtime_config", runtime_config)
        monkeypatch.setattr(assessment_worker, "judge_frozen_answer", judge)
        monkeypatch.setattr(assessment_worker, "OUTBOX_ROOT", tmp_path / "scoring-outbox")

        response = await client.post(url + "/submit", json={"answers": [
            {"order_no": 1, "text_answer": "先确认日志中的超时范围，限制重试并用幂等键检查副作用。"},
            {"order_no": 2, "text_answer": "先限定重试次数，再验证下游请求有没有重复写入。"},
            {"order_no": 3, "text_answer": "检索后核对证据与回答中的关键事实。"},
        ]})
        assert response.status_code == 202, response.text
        async with factory() as session:
            run = await session.get(ScoringRun, response.json()["task_id"])
            assert run.mode == "PRACTICE"
        for _ in range(3):
            async with factory() as session:
                claim = await assessment_jobs.claim_step(session, "experienced-fixture")
            await assessment_worker.execute_claim(claim, "experienced-fixture", factory)
        await assessment_worker.reconcile(factory)

        assert len(received) == 3
        assert [call["rubric"]["role_level"] for call in received] == ["mid", "senior", "目标层级尚未确认"]
        rubric = received[0]["rubric"]
        assert rubric["schema_version"] == 2 and rubric["role_level"] == "mid"
        assert rubric["calibration_status"] != "HUMAN_REVIEWED"
        assert [criterion["title"] for criterion in rubric["criteria"]] == CRITERIA
        assert [criterion["required_evidence"] for criterion in rubric["criteria"]] == [[c] for c in CRITERIA]

        result = (await client.get(url + "/report")).json()
        assert result["status"] == "COMPLETED" and result["score"] == 75
        assert result["experienced_bank_practice"] is True
        assert result["summary"]["mode"] == "PRACTICE" and result["summary"]["mastery_updated"] is False
        assert result["overall"] is None and result["observations"] == [] and result["learning_tasks"] == []
        frozen = result["questions"][0]["frozen"]
        assert frozen["assistance"]["reference_viewed"] is True
        assert frozen["independence"]["eligible_for_independent_verification"] is False
        unexposed = result["questions"][1]["frozen"]
        assert unexposed["assistance"]["reference_viewed"] is False
        assert unexposed["independence"]["eligible_for_independent_verification"] is False
        legacy_frozen = result["questions"][2]["frozen"]
        assert legacy_frozen["independence"]["canonical_question_id"] == legacy.id
        assert legacy_frozen["independence"]["eligible_for_independent_verification"] is False
        assert legacy_frozen["assessment_mode"] == "PRACTICE"
        titles = {entry["criterion_id"]: entry["title"] for entry in frozen["rubric"]["criteria"]}
        assert [titles[entry["criterion_id"]]
                for entry in result["questions"][0]["answer"]["feedback"]["criterion_scores"]] == CRITERIA

    async with factory() as session:
        exposures = (await session.scalars(select(Event).where(Event.kind == EVENT_KIND))).all()
        assert {(event.workspace_id, event.payload['question_id']) for event in exposures} == {
            (personal.id, first.id), (foreign.id, private.id),
        }
        assert len(exposures) == 2
        assert await session.scalar(select(func.count(Evidence.id)).where(Evidence.workspace_id == personal.id)) == 0
        assert await session.scalar(select(func.count(CompetencySnapshot.id)).where(
            CompetencySnapshot.workspace_id == personal.id)) == 0

@pytest.mark.parametrize("replacement", [
    {"topic": "eval"},
    {"title": "被替换的题目"},
    {"source_type": "USER_PRIVATE"},
    {"generation_meta": {"assessment_use": "PRACTICE_ONLY"}},
    {"workspace_id": 1},
    {"canonical_question_id": 2},
    {"is_demo": True},
])
def test_legacy_catalog_ignores_collisions(monkeypatch, tmp_path, replacement):
    originals = tmp_path / "bank.json"
    originals.write_text(json.dumps({"questions": [
        {"id": "LEGACY-Q1", "topic": "rag", "title": "旧题原文"},
    ]}), encoding="utf-8")
    mapping = tmp_path / "legacy_domains.json"
    mapping.write_text(json.dumps({"reliability": {"mid": ["LEGACY-Q1"], "senior": []}}))
    monkeypatch.setattr(seed, "BANK_PATH", originals)
    monkeypatch.setattr(experienced_bank, "LEGACY_DOMAINS_PATH", mapping)
    domain = experienced_bank.Domain(
        key="reliability", name="可靠性", order=1, focus="失败处理", questions=[],
    )
    row = Question(**{
        "id": 3, "short_code": "LEGACY-Q1", "title": "旧题原文", "topic": "rag",
        "qtype": "open", "source_type": "PUBLIC_LICENSED", "is_demo": False,
        **replacement,
    })
    assert experienced_bank.catalog_questions([domain], [row]) == {"reliability": []}

@pytest.mark.asyncio
async def test_shipped_default_bank_and_workspace_scope(isolated_seed, monkeypatch):
    factory, _ = isolated_seed
    server = Path(seed.__file__).resolve().parents[1]
    data = server / "app" / "data"
    monkeypatch.setattr(seed, "BANK_PATH", server.parent / "web" / "src" / "data" / "bank.json")
    monkeypatch.setattr(experienced_bank, "CATALOG_PATH", data / "experienced_bank")
    monkeypatch.setattr(experienced_bank, "LEGACY_DOMAINS_PATH", data / "legacy_domains.json")
    await seed.seed()
    async with factory() as session:
        personal = await session.scalar(select(Workspace).where(Workspace.slug == "personal"))
        foreign = Workspace(slug="foreign-scope", name="Foreign fixture", is_demo=False)
        session.add(foreign)
        await session.flush()
        skill = await session.scalar(select(Skill).where(Skill.key == "rag"))
        mine = other_question("SCOPE-MINE", skill.id, workspace_id=personal.id)
        second = other_question("SCOPE-SECOND", skill.id, workspace_id=personal.id)
        public = other_question("SCOPE-PUBLIC", skill.id)
        other = other_question("SCOPE-FOREIGN", skill.id, workspace_id=foreign.id)
        practice_only = other_question("SCOPE-PRACTICE", skill.id, workspace_id=personal.id,
                                       meta={"assessment_use": "PRACTICE_ONLY"})
        for row, company in [(mine, "Mine"), (second, "Mine"), (public, "Public"), (other, "Foreign")]:
            row.company = company
        other.topic = "foreign-topic"
        public.source_type = "PUBLIC_LICENSED"
        for row in (mine, second, other, practice_only):
            row.source_type = "USER_PRIVATE"
        session.add_all([mine, second, public, other, practice_only])
        await session.flush()
        variant = other_question("SCOPE-VARIANT", skill.id, workspace_id=personal.id)
        variant.canonical_question_id = mine.id
        session.add(variant)
        await session.commit()

    async def session_dependency():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(bank.router)
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[require_auth] = lambda: AuthenticatedPrincipal("fixture", "fixture", "OWNER", None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        pack = await client.get("/bank/experienced")
        assert pack.status_code == 200
        domains = pack.json()["domains"]
        assert len(domains) == 11
        codes = {row["short_code"] for domain in domains for row in domain["questions"]}
        expected = {q.code for domain in experienced_bank.load_catalog() for q in domain.questions}
        expected.update(q["id"] for q in json.loads(seed.BANK_PATH.read_text(encoding="utf-8"))["questions"])
        assert codes == expected
        assert sum(len(domain["questions"]) for domain in domains) == len(codes) == 227

        generic = (await client.get("/bank", params={"q": "SCOPE-"})).json()
        assert generic["total"] == 3
        assert {row["id"] for row in generic["items"]} == {mine.id, second.id, public.id}
        params = {"scope": "mine", "q": "SCOPE-", "skill": "rag", "limit": 1}
        first = (await client.get("/bank", params=params)).json()
        next_page = (await client.get("/bank", params={**params, "offset": 1})).json()
        assert first["total"] == next_page["total"] == 2
        assert [page["items"][0]["id"] for page in (first, next_page)] == [mine.id, second.id]
        assert first["facets"] == next_page["facets"] == {
            "companies": [{"name": "Mine", "count": 2}],
            "topics": [{"name": "rag", "count": 2}],
            "sources": [{"name": "USER_PRIVATE", "count": 2}],
        }
        filtered = await client.get("/bank", params={
            **params, "company": "min", "topic": "rag", "sources": ["USER_PRIVATE"],
        })
        assert filtered.json() == first
        foreign_page = (await client.get("/bank", params={"scope": "mine", "q": "SCOPE-"},
                                         headers={"X-Workspace": "foreign-scope"})).json()
        assert foreign_page["total"] == 1
        assert [row["id"] for row in foreign_page["items"]] == [other.id]
