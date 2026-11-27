"""Synthetic prices/usage, real isolated PostgreSQL, real billing boundary."""
import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.models import AppSetting, Trace
from app.models.private_model import PrivateModelCall
from app.services import llm
from app.services import private_model_billing as billing
from app.services.llm import AgentAdapter
from app.services.private_model_billing import BillingScope, BudgetPolicyError

CONFIG = {"provider": "synthetic", "base_url": "https://billing.invalid/v1", "model": "model-a", "api_key": "synthetic"}
SCOPE = BillingScope(1128, "synthetic:operation")
REAL_CLIENT = httpx.AsyncClient


@pytest_asyncio.fixture
async def metered(db_session, test_workspace, monkeypatch):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False, autoflush=False)
    async with factory.begin() as db:
        await db.execute(delete(PrivateModelCall))
    monkeypatch.setattr(billing, "SessionLocal", factory)
    import app.db
    monkeypatch.setattr(app.db, "SessionLocal", factory)
    settings = Settings(_env_file=None, private_llm_daily_budget_cny="0.05", private_llm_prices=[{
        **{key: CONFIG[key] for key in ("provider", "base_url", "model")},
        "input_cny_per_million": "2", "output_cny_per_million": "8",
    }])
    monkeypatch.setattr(billing, "get_settings", lambda: settings)
    monkeypatch.setattr(llm, "get_settings", lambda: settings)
    calls = []

    async def rows():
        async with factory() as db:
            return list((await db.scalars(select(PrivateModelCall).order_by(PrivateModelCall.created_at))).all())

    def transport(handler):
        async def dispatch(request):
            # Reserve must already be visible to another DB connection at network time.
            records = await rows()
            assert any(row.state == "RESERVED" for row in records)
            payload = json.loads(request.content)
            assert 0 < payload["max_tokens"] <= 32000
            calls.append(payload)
            response = handler(request)
            return await response if hasattr(response, "__await__") else response
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(dispatch), **kw))

    # Each test uses its own scope to keep durable ledger rows independent.
    scope = BillingScope(test_workspace.id, "synthetic:operation")
    return SimpleNamespace(settings=settings, factory=factory, rows=rows, transport=transport, calls=calls, scope=scope)


def response(usage=None, content="ok"):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}],
                                    **({"usage": usage} if usage is not None else {})})


@pytest.mark.asyncio
async def test_real_reserve_network_settle_separate_prices_and_metric(metered):
    metered.transport(lambda r: response({"prompt_tokens": 5000, "completion_tokens": 500, "total_tokens": 5500}))
    adapter = AgentAdapter()
    # 4616 + 384 template bytes reserve 5000 input tokens; only synthetic examples.
    result = await adapter.chat_with({**CONFIG, "output_limit": 2000}, "", "a" * 4616, billing_scope=metered.scope)
    assert result["content"] == "ok"
    call = (await metered.rows())[-1]
    assert call.reserved_nano_cny == 26_000_000
    assert call.actual_nano_cny == call.charged_nano_cny == 14_000_000
    assert call.state == "SETTLED" and call.operation == metered.scope.operation
    assert call.endpoint == CONFIG["base_url"] and call.max_tokens == metered.calls[0]["max_tokens"] == 2000
    assert adapter.last_response_meta["cost_basis"] == "configured_price"
    assert adapter.last_response_meta["cost_cny"] == 0.014
    from app.models import AgentMetric
    async with metered.factory() as db:
        metric = await db.scalar(select(AgentMetric).where(AgentMetric.billing_call_id == call.id))
        assert metric.workspace_id == metered.scope.workspace_id and metric.cost_cny == 0.014
        assert metric.cost_basis == "configured_price"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,code", [("price", "MODEL_PRICE_REQUIRED"), ("budget", "DAILY_BUDGET_REQUIRED"),
    ("scope", "BILLING_SCOPE_REQUIRED"), ("limit", "OUTPUT_LIMIT_REQUIRED"), ("endpoint", "MODEL_PRICE_REQUIRED"),
    ("fixture", "MODEL_PRICE_REQUIRED")])
async def test_missing_policy_denies_zero_network(metered, fault, code):
    metered.transport(lambda r: response())
    cfg, scope = dict(CONFIG), metered.scope
    if fault == "price": metered.settings.private_llm_prices = []
    if fault == "budget": metered.settings.private_llm_daily_budget_cny = "0"
    if fault == "scope": scope = None
    if fault == "limit": cfg["output_limit"] = 0
    if fault == "endpoint": cfg["base_url"] = "https://another.invalid/v1"
    if fault == "fixture": cfg["provider"] = "fixture"
    with pytest.raises(BudgetPolicyError) as exc:
        await AgentAdapter().chat_with(cfg, "s", "u", billing_scope=scope)
    assert exc.value.code == code and not metered.calls


@pytest.mark.asyncio
async def test_concurrent_last_budget_race_denied_before_network(metered):
    metered.settings.private_llm_daily_budget_cny = "0.026"
    arrived, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        arrived.set()
        await release.wait()
        return response()  # unknown usage retains the full reservation
    metered.transport(handler)
    async def run(scope=None):
        return await AgentAdapter().chat_with({**CONFIG, "output_limit": 2000}, "", "a" * 4616, billing_scope=scope or metered.scope)
    first = asyncio.create_task(run())
    await asyncio.wait_for(arrived.wait(), 3)
    competitors = await asyncio.gather(*(run(BillingScope(metered.scope.workspace_id + i + 1000, "other:workspace")) for i in range(8)), return_exceptions=True)
    assert all(isinstance(e, BudgetPolicyError) and e.status_code == 429 for e in competitors)
    assert all(int(e.headers["Retry-After"]) > 0 for e in competitors)
    assert len(metered.calls) == 1
    release.set()
    await first
    relevant = [r for r in await metered.rows() if r.workspace_id == metered.scope.workspace_id]
    assert len(relevant) == 1 and relevant[0].charged_nano_cny == 26_000_000


@pytest.mark.asyncio
async def test_frozen_prices_idempotent_settlement_and_original_china_day(metered, monkeypatch):
    monkeypatch.setattr(billing, "now", lambda: datetime(2026, 11, 28, 23, 59, 59, tzinfo=billing.CHINA))
    call_id = await billing.reserve(metered.scope, CONFIG, "", "a" * 4616, 2000)
    metered.settings.private_llm_prices[0]["input_cny_per_million"] = "200"
    metered.settings.private_llm_prices[0]["output_cny_per_million"] = "800"
    monkeypatch.setattr(billing, "now", lambda: datetime(2026, 11, 29, 0, 1, tzinfo=billing.CHINA))
    results = await asyncio.gather(*(billing.settle(call_id, {"prompt_tokens": 5000, "completion_tokens": 500}) for _ in range(5)))
    assert {r.actual_nano_cny for r in results} == {14_000_000}
    again = await billing.settle(call_id, {"prompt_tokens": 1, "completion_tokens": 1})
    assert again.actual_nano_cny == 14_000_000
    call = next(r for r in await metered.rows() if r.id == call_id)
    assert str(call.day) == "2026-11-28"
    assert call.input_price_nano_per_million == 2_000_000_000
    assert call.output_price_nano_per_million == 8_000_000_000


@pytest.mark.asyncio
async def test_business_rollback_cannot_erase_charge_or_block_on_trace(metered):
    metered.transport(lambda r: response({"prompt_tokens": 5, "completion_tokens": 7}))
    async with metered.factory() as business:
        # An uncommitted business row with no financial FK must not block reserve.
        business.add(AppSetting(key="synthetic-business-rollback", value={"draft": True}))
        trace = Trace(id=999_999, workspace_id=metered.scope.workspace_id, kind="test", title="synthetic")
        await asyncio.wait_for(AgentAdapter().chat_with(CONFIG, "s", "u", trace=trace, session=business), 5)
        await business.rollback()
    relevant = [r for r in await metered.rows() if r.workspace_id == metered.scope.workspace_id]
    assert relevant[0].state == "SETTLED" and relevant[0].charged_nano_cny == 66_000
    async with metered.factory() as db:
        assert await db.get(AppSetting, "synthetic-business-rollback") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("usage", [None, {"total_tokens": 12}, {"prompt_tokens": 2},
    {"prompt_tokens": True, "completion_tokens": 3}, {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 4},
    {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    {"prompt_tokens": 2, "completion_tokens": 0},
    {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": True}])
async def test_unknown_usage_keeps_reserve_forever_no_ttl_refund(metered, monkeypatch, usage):
    metered.transport(lambda r: response(usage))
    await AgentAdapter().chat_with(CONFIG, "s", "u", billing_scope=metered.scope)
    call = (await metered.rows())[-1]
    assert call.state == "UNKNOWN" and call.actual_nano_cny is None
    assert call.charged_nano_cny == call.reserved_nano_cny > 0
    monkeypatch.setattr(billing, "now", lambda: datetime(2030, 1, 1, tzinfo=billing.CHINA))
    repeat = await billing.settle(call.id, {"prompt_tokens": 0, "completion_tokens": 0})
    assert repeat.state == "UNKNOWN" and repeat.charged_nano_cny == call.reserved_nano_cny


class Body(httpx.AsyncByteStream):
    def __init__(self, *, usage=False, stall=False):
        self.usage, self.stall = usage, stall
        self.started = asyncio.Event()

    async def __aiter__(self):
        yield b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n'
        if self.usage:
            yield b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":4,"total_tokens":7}}\n\n'
        self.started.set()
        if self.stall:
            await asyncio.Event().wait()
        yield b'data: {broken json\n\n'

    async def aclose(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("has_usage,cancel", [(True, False), (False, False), (True, True), (False, True)])
async def test_failed_or_cancelled_stream_retains_usage_and_financial_truth(metered, has_usage, cancel):
    body = Body(usage=has_usage, stall=cancel)
    metered.transport(lambda r: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body))
    adapter = AgentAdapter()
    pending = asyncio.create_task(adapter.chat_with(CONFIG, "s", "u", stream=True, billing_scope=metered.scope))
    if cancel:
        await asyncio.wait_for(body.started.wait(), 3)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert adapter.last_error == {"kind": "cancelled"}
    else:
        assert await pending is None
    call = (await metered.rows())[-1]
    assert call.state == ("SETTLED" if has_usage else "UNKNOWN")
    assert call.charged_nano_cny == (38_000 if has_usage else call.reserved_nano_cny)
    assert (adapter.last_usage or {}).get("total_tokens") == (7 if has_usage else None)


@pytest.mark.asyncio
async def test_actual_usage_over_reservation_is_truth_and_blocks_next_call(metered):
    metered.settings.private_llm_daily_budget_cny = "0.001"
    metered.transport(lambda r: response({"prompt_tokens": 5000, "completion_tokens": 500}))
    cfg = {**CONFIG, "output_limit": 1}
    await AgentAdapter().chat_with(cfg, "s", "u", billing_scope=metered.scope)
    call = (await metered.rows())[-1]
    assert call.actual_nano_cny == 14_000_000 > call.reserved_nano_cny
    with pytest.raises(BudgetPolicyError) as exc:
        await AgentAdapter().chat_with(cfg, "s", "u", billing_scope=BillingScope(metered.scope.workspace_id + 1, "another:workspace"))
    assert exc.value.status_code == 429 and len(metered.calls) == 1


@pytest.mark.asyncio
async def test_small_overrun_blocks_day_even_with_large_budget_and_new_workspace(metered, monkeypatch):
    metered.settings.private_llm_daily_budget_cny = "10"
    monkeypatch.setattr(billing, "now", lambda: datetime(2026, 11, 28, 12, tzinfo=billing.CHINA))
    # Input ceiling is 386; actual prompt is 400. Actual cost is only 0.000808 CNY.
    metered.transport(lambda r: response({"prompt_tokens": 400, "completion_tokens": 1}))
    cfg = {**CONFIG, "output_limit": 1}
    await AgentAdapter().chat_with(cfg, "s", "u", billing_scope=metered.scope)
    record = (await metered.rows())[-1]
    assert record.reserved_nano_cny < record.actual_nano_cny < billing.money("10")
    with pytest.raises(BudgetPolicyError) as exc:
        await AgentAdapter().chat_with(cfg, "s", "u", billing_scope=BillingScope(999_991, "another:workspace"))
    assert exc.value.code == "RESERVATION_EXCEEDED" and len(metered.calls) == 1
    assert int(exc.value.headers["Retry-After"]) > 0
    monkeypatch.setattr(billing, "now", lambda: datetime(2026, 11, 29, 0, 1, tzinfo=billing.CHINA))
    # Original-day anomaly remains in its own billing day; the next day can admit.
    next_call = await billing.reserve(metered.scope, cfg, "s", "u", 1)
    assert next_call != record.id


@pytest.mark.asyncio
async def test_each_fallback_and_repair_has_distinct_real_reservation(metered):
    from app.services.question_generation import generate_response
    async def config(self): return CONFIG
    from unittest.mock import patch
    metered.transport(lambda r: response({"prompt_tokens": 3, "completion_tokens": 4}, '{"ok":true}'))
    with patch.object(AgentAdapter, "_effective", config):
        await generate_response("initial", billing_scope=metered.scope)
        await generate_response("repair", billing_scope=metered.scope)
    calls = [r for r in await metered.rows() if r.workspace_id == metered.scope.workspace_id]
    assert len(calls) == len(metered.calls) == 2 and len({r.id for r in calls}) == 2
    assert all(r.state == "SETTLED" and r.charged_nano_cny == 38_000 for r in calls)


@pytest.mark.asyncio
async def test_legacy_empty_project_budget_merges_enforced_fields(metered):
    from app.services.project_autopilot_config import DEFAULT_BUDGET, SOURCE_NOTICE, settings_for
    async with metered.factory() as db:
        db.add(AppSetting(key=f"project-autopilot:{metered.scope.workspace_id}", value={"budget": {}}))
        await db.commit()
        config = await settings_for(db, metered.scope.workspace_id)
    assert config["budget"] == DEFAULT_BUDGET
    assert DEFAULT_BUDGET["max_model_calls"] > 0 and DEFAULT_BUDGET["max_source_bytes"] > 0
    assert "默认不限" not in SOURCE_NOTICE


@pytest.mark.asyncio
async def test_workspace_deleted_charge_still_blocks_entire_deployment(metered):
    from app.models import Workspace
    metered.settings.private_llm_daily_budget_cny = "0.026"
    metered.transport(lambda r: response())
    await AgentAdapter().chat_with({**CONFIG, "output_limit": 2000}, "", "a" * 4616, billing_scope=metered.scope)
    async with metered.factory.begin() as db:
        await db.execute(delete(Workspace).where(Workspace.id == metered.scope.workspace_id))
    records = await metered.rows()
    assert len(records) == 1 and records[0].charged_nano_cny == 26_000_000
    with pytest.raises(BudgetPolicyError) as exc:
        await AgentAdapter().chat_with(CONFIG, "s", "u", billing_scope=BillingScope(999_998, "new:workspace"))
    assert exc.value.status_code == 429 and len(metered.calls) == 1


@pytest.mark.asyncio
async def test_explicit_zero_price_is_configured_and_differs_from_invalid_zero_usage(metered):
    metered.settings.private_llm_prices[0].update(input_cny_per_million="0", output_cny_per_million="0")
    metered.transport(lambda r: response({"prompt_tokens": 5, "completion_tokens": 3}))
    adapter = AgentAdapter()
    await adapter.chat_with(CONFIG, "s", "u", billing_scope=metered.scope)
    call = (await metered.rows())[-1]
    assert call.state == "SETTLED" and call.actual_nano_cny == 0
    assert adapter.last_response_meta["cost_basis"] == "configured_price"


@pytest.mark.asyncio
async def test_connection_test_surface_429_retry_after_and_trusted_workspace(metered, monkeypatch, test_workspace):
    from unittest.mock import AsyncMock

    from fastapi import FastAPI

    from app.api.deps import get_workspace, require_local_owner
    from app.api.v1 import settings
    app = FastAPI()
    app.include_router(settings.router)
    app.dependency_overrides[get_workspace] = lambda: test_workspace
    app.dependency_overrides[require_local_owner] = lambda: None
    monkeypatch.setattr(settings, "get_llm_config", AsyncMock(return_value=CONFIG))
    metered.settings.private_llm_daily_budget_cny = "0.0001"
    metered.transport(lambda r: response())
    async with REAL_CLIENT(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/settings/llm/test", json={})
    assert result.status_code == 429 and int(result.headers["Retry-After"]) > 0
    assert result.json()["detail"]["code"] == "DAILY_BUDGET_EXHAUSTED"
    assert not metered.calls


@pytest.mark.asyncio
async def test_billing_db_unavailable_denies_without_network(metered, monkeypatch):
    from sqlalchemy.exc import OperationalError
    class Unavailable:
        def begin(self): raise OperationalError("synthetic", {}, Exception("synthetic"))
    monkeypatch.setattr(billing, "SessionLocal", Unavailable())
    metered.transport(lambda r: response())
    with pytest.raises(BudgetPolicyError) as exc:
        await AgentAdapter().chat_with(CONFIG, "s", "u", billing_scope=metered.scope)
    assert exc.value.code == "BILLING_UNAVAILABLE" and not metered.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["answer", "import", "memory", "interview_rewrite", "generation", "legacy_judge",
    "assessment_worker", "teaching", "teaching_help", "notes", "podcast", "project_analysis", "autopilot"])
async def test_paid_service_paths_reach_real_gate_with_trusted_scope(metered, monkeypatch, path):
    """Stop after transport/schema validation; accounting is real, content is synthetic."""
    import time

    from app.api.v1 import bank, memory
    from app.models import LearningTask
    from app.services import (
        answer_gen,
        assessment_worker,
        interview_rewrite,
        llm_judge,
        podcast_content,
        project_autopilot_engine,
        project_deep_analysis,
        question_generation,
        study_notes,
        teaching,
    )
    from app.services.assessment_rubric import compile_rubric
    monkeypatch.setattr(llm, "_CACHE", dict(CONFIG))
    monkeypatch.setattr(llm, "_CACHE_AT", time.monotonic())
    async def configured(self): return dict(CONFIG)
    monkeypatch.setattr(AgentAdapter, "_effective", configured)
    for key, value in CONFIG.items():
        if hasattr(metered.settings, f"llm_{key}"):
            setattr(metered.settings, f"llm_{key}", value)
    monkeypatch.setattr(podcast_content, "get_settings", lambda: metered.settings)
    metered.settings.private_llm_daily_budget_cny = "5"
    metered.transport(lambda r: response({"prompt_tokens": 5, "completion_tokens": 3}, "{}"))
    scope = metered.scope
    async with metered.factory() as db:
        trace = Trace(id=888_888, workspace_id=scope.workspace_id, kind="synthetic", title="scope fixture")
        task = LearningTask(id=123, workspace_id=scope.workspace_id, title="Explain retrieval", kind="UNDERSTAND", content={})
        try:
            if path == "answer":
                await answer_gen.generate_answer("Explain retrieval", "rag", "RAG", billing_scope=scope)
            elif path == "import":
                await bank._extract_questions("Explain retrieval", billing_scope=scope)
            elif path == "memory":
                await memory._extract_memories("I prefer detailed examples", billing_scope=scope)
            elif path == "interview_rewrite":
                await interview_rewrite.rewrite_interview_questions("synthetic", [], {"fact": {"text": "synthetic"}}, billing_scope=scope)
            elif path == "generation":
                await question_generation.generate_response("synthetic", billing_scope=scope)
            elif path == "legacy_judge":
                await llm_judge.judge_open_answer(AgentAdapter(), title="Explain retrieval", skill_name="RAG",
                    rubric={"max_points": 5}, text="synthetic answer", trace=trace, session=db)
            elif path == "assessment_worker":
                rubric = compile_rubric({"max_points": 5, "points": [{"title": "mechanism", "detail": "explain mechanism"}]},
                                        question_id=1, title="Explain retrieval")
                claim = SimpleNamespace(workspace_id=scope.workspace_id, run_id=12, step_id=13, payload={
                    "qtype": "open", "question_id": 1, "answer_revision_id": 1, "title": "Explain retrieval",
                    "rubric": rubric, "submitted_answer": {"text_answer": "synthetic answer"},
                })
                await assessment_worker.evaluate(claim, CONFIG)
            elif path == "teaching":
                await teaching.build_teaching_content(db, AgentAdapter(), task=task, skill_name="RAG", skill_key="rag")
            elif path == "teaching_help":
                await teaching.help_answer(db, AgentAdapter(), task=task, skill_name="RAG", skill_key="rag", mode=next(iter(teaching.HELP_MODES)))
            elif path == "notes":
                await study_notes.generate_cards("synthetic note", billing_scope=scope)
            elif path == "podcast":
                await podcast_content.make_scripts(db, scope.workspace_id, title="synthetic", answer="synthetic answer",
                                                   topic="retrieval", series=False, source="synthetic")
            elif path == "project_analysis":
                await project_deep_analysis._ask_model(CONFIG, "synthetic", "synthetic instruction", {}, [], scope)
            elif path == "autopilot":
                runtime = project_autopilot_engine.RuntimeContext(scope.workspace_id, 12, CONFIG, {})
                await project_autopilot_engine._ask("plan_modules", {}, project_autopilot_engine.PlanResponse, runtime)
        except Exception as exc:
            # Synthetic content need not pass each business schema. A missing
            # scope or price is never an acceptable way to reach this assertion.
            assert not isinstance(exc, BudgetPolicyError), str(exc)
        await db.rollback()
    assert len(metered.calls) == 1, path
    records = await metered.rows()
    assert len(records) == 1 and records[0].workspace_id == scope.workspace_id
    assert records[0].operation and records[0].state in ("SETTLED", "UNKNOWN")


@pytest.mark.asyncio
@pytest.mark.parametrize("flow", ["artifact_repair", "provider_fallback", "policy_denial", "settlement_failure"])
async def test_chat_actual_repair_fallback_and_policy_terminal(metered, monkeypatch, test_workspace, flow):
    from unittest.mock import AsyncMock

    from fastapi import BackgroundTasks

    from app.api.v1 import study_chat as api
    from app.models.study_chat import StudyMessage
    from app.services import study_chat as worker
    settings = metered.settings
    settings.private_llm_daily_budget_cny = "5"
    settings.study_strong_model = "model-b" if flow == "provider_fallback" else ""
    settings.study_timeout_seconds = 60
    settings.private_llm_prices.append({**settings.private_llm_prices[0], "model": "model-b"})
    monkeypatch.setattr(worker, "SessionLocal", metered.factory)
    monkeypatch.setattr(worker, "get_llm_config", AsyncMock(return_value=CONFIG))
    monkeypatch.setattr(worker, "get_settings", lambda: settings)

    def handler(request):
        if flow == "provider_fallback" and len(metered.calls) == 1:
            return httpx.Response(503)
        if flow == "policy_denial":
            # Simulate an operator lowering the daily limit while call one runs.
            settings.private_llm_daily_budget_cny = "0.000034"
        if flow == "settlement_failure":
            from sqlalchemy.exc import OperationalError
            class Unavailable:
                def begin(self): raise OperationalError("synthetic", {}, Exception("synthetic"))
            monkeypatch.setattr(billing, "SessionLocal", Unavailable())
        body = (b'data: {"choices":[{"index":0,"delta":{"content":"synthetic plain answer"}}]}\n\n'
                b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}\n\n'
                b'data: [DONE]\n\n')
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    metered.transport(handler)
    async with metered.factory() as db:
        sid = (await api.create(api.StartSession(topic="synthetic", mode="auto"), db, test_workspace))["id"]
        tasks = BackgroundTasks()
        sent = await api.send(sid, api.NewMessage(request_id="synthetic-chat-billing", content="解释检索",
            action="explain" if flow == "provider_fallback" else "demo"), tasks, db, test_workspace)
    await asyncio.wait_for(tasks(), 15)
    async with metered.factory() as db:
        message = await db.get(StudyMessage, sent["assistant_message_id"])
        trace = message.coach["trace"]
    records = await metered.rows()
    assert all(r.workspace_id == test_workspace.id and r.operation.startswith("study_message:") for r in records)
    if flow == "settlement_failure":
        assert len(records) == len(metered.calls) == len(trace["calls"]) == 1
        assert records[0].state == "RESERVED" and records[0].charged_nano_cny == records[0].reserved_nano_cny
        assert message.status == "failed" and trace["policy_phase"] == "settlement"
        assert trace["policy_code"] == "BILLING_UNAVAILABLE"
        call = trace["calls"][0]
        assert call["outcome"] == "unknown" and call["billing_phase"] == "settlement"
        assert call["billing_call_id"] == records[0].id
        assert call["usage"] == {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
    elif flow == "policy_denial":
        assert len(records) == len(metered.calls) == 1
        assert message.status == "failed" and trace["error_kind"] == "budget_policy"
        assert trace["policy_code"] == "DAILY_BUDGET_EXHAUSTED" and trace["calls"][-1]["outcome"] == "denied"
    else:
        assert len(records) == len(metered.calls) == 2 and len({r.id for r in records}) == 2
        assert trace["calls"][1]["retry_kind"] == ("artifact_repair" if flow == "artifact_repair" else "transport_failover")
        assert records[1].state == "SETTLED"
        assert records[0].state == ("UNKNOWN" if flow == "provider_fallback" else "SETTLED")


@pytest.mark.asyncio
async def test_settlement_failure_preserves_committed_reservation(metered, monkeypatch):
    from sqlalchemy.exc import OperationalError
    class Unavailable:
        def begin(self): raise OperationalError("synthetic", {}, Exception("synthetic"))
    def handler(request):
        monkeypatch.setattr(billing, "SessionLocal", Unavailable())
        return response({"prompt_tokens": 5, "completion_tokens": 3})
    metered.transport(handler)
    with pytest.raises(BudgetPolicyError) as exc:
        await AgentAdapter().chat_with(CONFIG, "s", "u", billing_scope=metered.scope)
    assert exc.value.code == "BILLING_UNAVAILABLE" and len(metered.calls) == 1
    assert exc.value.phase == "settlement" and exc.value.billing_call_id
    call = (await metered.rows())[-1]
    assert call.state == "RESERVED" and call.charged_nano_cny == call.reserved_nano_cny > 0


@pytest.mark.asyncio
async def test_slow_real_billing_admission_does_not_consume_provider_first_content_deadline(metered, monkeypatch):
    actual_reserve = llm.reserve
    async def delayed_reserve(*args, **kwargs):
        call_id = await actual_reserve(*args, **kwargs)
        await asyncio.sleep(0.3)
        return call_id
    monkeypatch.setattr(llm, "reserve", delayed_reserve)
    raw = (b'data: {"choices":[{"index":0,"delta":{"content":"ok"}}]}\n\n'
           b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
           b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":4,"total_tokens":7}}\n\n'
           b'data: [DONE]\n\n')
    metered.transport(lambda r: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=raw))
    result = await AgentAdapter().chat_with(CONFIG, "s", "u", stream=True, first_content_timeout=0.2,
                                           billing_scope=metered.scope)
    assert result["content"] == "ok" and len(metered.calls) == 1
    assert (await metered.rows())[-1].state == "SETTLED"


@pytest.mark.asyncio
async def test_autopilot_settlement_failure_retains_started_request_usage_and_billing_id(metered, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.services import project_autopilot_engine as engine
    class Unavailable:
        def begin(self): raise OperationalError("synthetic", {}, Exception("synthetic"))
    def handler(request):
        monkeypatch.setattr(billing, "SessionLocal", Unavailable())
        return response({"prompt_tokens": 5, "completion_tokens": 3}, "{}")
    metered.transport(handler)
    runtime = engine.RuntimeContext(metered.scope.workspace_id, 12, CONFIG, {})
    with pytest.raises(engine.StageError) as exc:
        await engine._ask("plan_modules", {}, engine.PlanResponse, runtime)
    call = (await metered.rows())[-1]
    assert exc.value.category == "BILLING_UNAVAILABLE" and call.state == "RESERVED"
    assert exc.value.request_meta["request_started"] is True
    assert exc.value.request_meta["billing_phase"] == "settlement"
    assert exc.value.request_meta["billing_call_id"] == call.id
    assert exc.value.usage == {"prompt_tokens": 5, "completion_tokens": 3}
    assert len(metered.calls) == 1
