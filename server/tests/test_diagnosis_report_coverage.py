"""Deep explanations are supplemental to the frozen assessment."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_core_scoring import db_questions

from app.api.v1 import exams
from app.config import get_settings
from app.main import app
from app.models import Evidence, Exam, ScoringRun
from app.services import assessment_jobs, assessment_worker


async def completed_exam(session, workspace, outbox):
    _, questions = await db_questions(session, workspace)
    detail = await exams.create_exam(
        exams.CreateExamReq(kind=Exam.KIND_BANK, question_ids=[questions[0].id, questions[2].id]),
        workspace, session,
    )
    exam_id = detail["id"]
    ack = await exams.submit_exam(
        exam_id, workspace, session,
        exams.SubmitExamReq(answers=[
            exams.SaveAnswerReq(order_no=1, choice_index=0),
            exams.SaveAnswerReq(order_no=2, choice_index=1),
        ]),
    )
    factory = async_sessionmaker(session.bind, expire_on_commit=False, autoflush=False)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(assessment_worker, "OUTBOX_ROOT", outbox)
        for _ in range(2):
            async with factory() as worker_session:
                claim = await assessment_jobs.claim_step(worker_session, "coverage-fixture")
            assert claim is not None and claim.run_id == ack["task_id"]
            assert claim.payload["qtype"] == "mcq"
            await assessment_worker.execute_claim(claim, "coverage-fixture", factory)
        await assessment_worker.reconcile(factory)
    return exam_id, ack["task_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("model_outputs", "missing", "status"), [
    ([{"1": {"why": "Reason 1", "how": "Advice 1"}},
      {"1": {"why": "Unsolicited replacement", "how": "Replacement"},
       "Q2": {"why": " ", "how": "Incomplete entry"}, "3": {"why": "Extra", "how": "Extra"}}],
     [2], "PARTIAL"),
    ([{"1": {"why": "Reason 1", "how": "Advice 1"},
       "2": {"why": "Reason 2", "how": "Advice 2"}}], [], "SUCCESS"),
    ([{"1": {"why": "Reason 1", "how": "Advice 1"}},
      {"Q2": {"why": "Reason 2", "how": "Advice 2"}}], [], "SUCCESS"),
])
async def test_deep_coverage_preserves_official_assessment(
    db_session, test_workspace, monkeypatch, tmp_path, model_outputs, missing, status,
):
    exam_id, run_id = await completed_exam(db_session, test_workspace, tmp_path / "outbox")
    run = await db_session.get(ScoringRun, run_id)
    frozen_report = deepcopy(run.report)
    assert frozen_report["score"] == 50
    evidence_query = select(Evidence.source_ref, Evidence.grade, Evidence.meta).where(
        Evidence.workspace_id == test_workspace.id,
    ).order_by(Evidence.id)
    evidence = (await db_session.execute(evidence_query)).all()
    assert [row.source_ref for row in evidence] == [f"exam:{exam_id}#q1", f"exam:{exam_id}#q2"]

    chat = AsyncMock(side_effect=[
        {"content": json.dumps({"per_question": output})} for output in model_outputs
    ])
    monkeypatch.setattr(exams, "llm_adapter", SimpleNamespace(
        active=True, _effective=AsyncMock(return_value={"model": "fixture"}), chat=chat,
    ))
    path = f"/api/v1/exams/{exam_id}/report"
    headers = {"X-Api-Token": get_settings().api_token, "X-Workspace": test_workspace.slug}
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)

    async def test_session():
        async with factory() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, exams.get_session, test_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as client:
        before = (await client.get(path)).raise_for_status().json()
        generated = (await client.post(f"{path}/deep")).raise_for_status().json()
        assert generated["coverage"] == {"total": 2, "explained": 2 - len(missing), "missing_order_nos": missing}
        assert list(sorted(generated["per_question"])) == (["1"] if missing else ["1", "2"])
        assert generated["per_question"]["1"]["why"] == "Reason 1"
        assert chat.await_count == len(model_outputs)
        if len(model_outputs) == 2:
            retry_prompt = chat.await_args.args[1]
            assert "【Q2】" in retry_prompt and "【Q1】" not in retry_prompt

        refreshed = (await client.get(path)).raise_for_status().json()
        assert refreshed.pop("diagnosis_deep") == generated
        assert refreshed.pop("has_deep") is True
        assert refreshed == {k: v for k, v in before.items() if k not in {"diagnosis_deep", "has_deep"}}
        traces = (await client.get("/api/v1/observatory/traces")).raise_for_status().json()["items"]
        trace = next(item for item in traces if item["kind"] == "diagnosis_deep")
        assert trace["status"] == status

        # Reports saved before CP05 have no coverage field.
        exam = await db_session.get(Exam, exam_id, populate_existing=True)
        legacy = {key: value for key, value in generated.items() if key != "coverage"}
        legacy["per_question"] = {f"Q{key}": value for key, value in legacy["per_question"].items()}
        exam.summary = {**exam.summary, "diagnosis_deep": legacy}
        await db_session.commit()
        assert (await client.get(path)).raise_for_status().json()["diagnosis_deep"] == generated

    await db_session.refresh(run)
    assert run.report == frozen_report
    assert (await db_session.execute(evidence_query)).all() == evidence


pytestmark = pytest.mark.usefixtures("enrolled_default_owner")
