"""Exercise the production study reply worker with isolated database rows and synthetic SSE."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_llm_stream_transport import CONFIG, DONE, Body, frame, install

from app import db
from app.api.v1 import study_chat as api
from app.config import get_settings
from app.i18n import using_language
from app.models import AppSetting, Project
from app.services import llm
from app.services import study_chat as worker
from app.services.secret_storage import seal_setting


def use_relay_transport(monkeypatch, relay):
    # Replace the native caller's transport only. The relay's own explicit
    # upstream MockTransport must keep its separate credential/routing boundary.
    monkeypatch.setattr("app.services.model_outbound.PinnedModelTransport",
                        lambda endpoint, policy: httpx.ASGITransport(app=relay))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 403, 429])
async def test_registered_turn_preserves_upstream_retry_class(study, monkeypatch, status):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from deploy.tenant.model_relay import RelayConfig, create_app

    ask, _ = study
    token = "synthetic-only-tenant-token-for-this-test"
    base_model = CONFIG["model"]
    for field, value in {**CONFIG, "base_url": "http://relay.test/v1", "api_key": token}.items():
        monkeypatch.setattr(get_settings(), f"llm_{field}", value)
    monkeypatch.setattr(get_settings(), "model_local_origins", ["http://relay.test"])
    requested = []

    async def upstream(request):
        requested.append(json.loads(request.content)["model"])
        if len(requested) == 1:
            return httpx.Response(status, json={"detail": "private provider detail"})
        return response("合成答复。")

    relay = create_app(RelayConfig("https://provider.test/v1", base_model, "synthetic-upstream-key", token),
                       strong_model="fixture-pro", transport=httpx.MockTransport(upstream))
    use_relay_transport(monkeypatch, relay)
    reply = await ask("什么是分母？")
    assert requested == ([base_model, "fixture-pro"] if status == 429 else [base_model])
    assert reply["status"] == ("completed" if status == 429 else "failed")
    assert reply["trace"]["calls"][0]["http_status"] == status
    assert "private provider detail" not in json.dumps(reply, default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize(("strong_url", "strong_model", "mode", "keys"), [
    ("https://PROVIDER.test:443/v1/", "quick", "auto", ["flash-key"]),
    ("https://PROVIDER.test:443/v1/", "quick", "strong", ["strong-key"]),
    ("https://provider.test/v1", "deep", "auto", ["flash-key", "strong-key"]),
    ("https://other.test/v1", "quick", "auto", ["flash-key", "strong-key"]),
])
async def test_registered_retry_uses_distinct_physical_models(study, monkeypatch, strong_url, strong_model, mode, keys):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from deploy.tenant.model_relay import RelayConfig, create_app
    from deploy.tenant.runtime import _compose

    ask, settings = study
    token = "synthetic-only-tenant-token-for-this-test"
    base = "https://provider.test/v1"
    upstream = {"MODEL_UPSTREAM_MODEL":"quick", "MODEL_UPSTREAM_BASE_URL":base, "MODEL_UPSTREAM_API_KEY":"flash-key",
                "MODEL_STRONG_MODEL":strong_model, "MODEL_STRONG_BASE_URL":strong_url, "MODEL_STRONG_API_KEY":"strong-key"}
    networks = {"private":"10.9.0.0/28","egress":"10.9.0.16/28","repo_egress":"10.9.0.32/28"}
    env = _compose("seed-fixture","采购","auth","db",networks,upstream,token,"repo")["services"]["api"]["environment"]
    for field, value in {"provider":"openai","api_key":token,"base_url":env["LLM_BASE_URL"],"model":"quick"}.items():
        monkeypatch.setattr(settings, f"llm_{field}", value)
    for field in ("study_strong_model","study_strong_base_url","study_strong_api_key","study_strong_provider"):
        monkeypatch.setattr(settings, field, env[field.upper()])
    monkeypatch.setattr(settings, "model_local_origins", json.loads(env["MODEL_LOCAL_ORIGINS"]))
    monkeypatch.setattr(settings,"study_relay_same_upstream",env["STUDY_RELAY_SAME_UPSTREAM"] == "true")
    seen = []

    async def provider(request):
        seen.append(request.headers["authorization"].removeprefix("Bearer "))
        return response("合成答复。") if seen[-1] == "strong-key" else httpx.Response(429)

    relay = create_app(RelayConfig(base,"quick","flash-key",token),
                       strong_connection=RelayConfig(strong_url,strong_model,"strong-key",token),
                       transport=httpx.MockTransport(provider))
    use_relay_transport(monkeypatch, relay)
    reply = await ask("什么是订单？", mode=mode)
    assert seen == keys
    assert reply["status"] == ("completed" if keys[-1] == "strong-key" else "failed")


@pytest.mark.asyncio
@pytest.mark.parametrize(("explicit_flash", "partial_strong", "expected_flash", "expected_strong"), [
    ("", False, "saved-choice", "strong-choice"),
    ("quick-choice", False, "quick-choice", "strong-choice"),
    ("", True, "saved-choice", ""),
])
async def test_personal_model_launcher_inherits_native_profiles(
    monkeypatch, tmp_path, explicit_flash, partial_strong, expected_flash, expected_strong,
):
    import asyncpg
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2]))
    from deploy.tenant.start_local import environment

    settings = get_settings()
    for name, value in {
        "llm_model": "retired-default", "llm_base_url": CONFIG["base_url"], "llm_api_key": CONFIG["api_key"],
        "study_flash_model": explicit_flash, "study_strong_model": "strong-choice",
        "study_strong_provider": "incomplete" if partial_strong else "", "study_strong_base_url": "",
        "study_strong_api_key": "",
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(llm, "get_llm_config", AsyncMock(return_value={**CONFIG, "model": "saved-choice"}))
    monkeypatch.setattr(asyncpg, "connect", AsyncMock(return_value=AsyncMock()))
    account_env = tmp_path / "account.env"
    account_env.write_text("DATABASE_URL=postgresql://localhost/registered_test")
    args = SimpleNamespace(account_env=account_env, state_dir=tmp_path, bind_owner=None, port=1,
                           use_personal_model=True, library_dir=[], local_unmetered=False)
    env = await environment(args)
    assert env["SEED_STUDY_FLASH_MODEL"] == expected_flash
    assert env["SEED_STUDY_STRONG_MODEL"] == expected_strong
    assert not env["SEED_STUDY_STRONG_BASE_URL"] and not env["SEED_STUDY_STRONG_API_KEY"]


@pytest.fixture
def study(monkeypatch, db_session, test_workspace, secrets_key, mock_private_billing):
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(db, "SessionLocal", factory)
    settings = get_settings()
    for name, value in {
        "study_flash_model": "",
        "study_flash_thinking": "",
        "study_strong_model": "fixture-pro",
        "study_strong_provider": "",
        "study_strong_api_key": "",
        "study_strong_base_url": "",
        "study_flash_capabilities": ["text", "stream"],
        "study_strong_capabilities": ["text", "stream", "structured_text", "code_reasoning"],
        "study_flash_context": 24000,
        "study_strong_context": 48000,
        "study_flash_output_tokens": 6000,
        "study_strong_output_tokens": 12000,
        "study_output_budget": 16000,
        "study_timeout_seconds": 60,
    }.items():
        monkeypatch.setattr(settings, name, value)
    for field, value in CONFIG.items():
        monkeypatch.setattr(settings, f"llm_{field}", value)

    async def ask(question, *, sid=None, project_id=None, **kwargs):
        if sid is None:
            sid = (await api.create(api.StartSession(project_id=project_id), db_session, test_workspace))["id"]
        tasks = BackgroundTasks()
        ticket = await api.send(sid, api.NewMessage(content=question, request_id=uuid4().hex, **kwargs),
                                tasks, db_session, test_workspace)
        await tasks()
        reply = next(m for m in (await api.detail(sid, db_session, test_workspace))["messages"]
                     if m["id"] == ticket["assistant_message_id"])
        return reply

    return ask, settings


def response(content):
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          stream=Body([frame(content), frame(finish="stop"), DONE]))


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["en", "zh-CN"])
@pytest.mark.parametrize("path", ["transport_failover", "artifact_repair", "manual_retry"])
async def test_native_chat_envelope_uses_the_admitted_language_on_every_call(
    study, monkeypatch, db_session, test_workspace, language, path,
):
    ask, _ = study
    english = language == "en"
    opposite = "zh-CN" if english else "en"
    previous = "岗位原文写着「复现故障」。" if english else 'The posting says "reproduce failures."'
    prior_reply = "先记下原文。" if english else "Keep that exact wording."
    current = ("请用两句话说说如何确定岗位最看重的能力。" if english
               else "Explain how to identify the skill a role values most. Please respond in English.")
    source = {"kind": "fixture", "id": 42, "label": "Original", "text": previous}

    async def gather(_session, turn, _timeout, _save):
        turn["sources"] = [source]

    monkeypatch.setattr(worker, "_gather_sources", gather)
    replies = [response(prior_reply)]

    async def provider(_request):
        return replies.pop(0)

    requests = install(monkeypatch, None, handler=provider)
    test_workspace.target_role = "Synthetic QA engineer"
    await db_session.commit()
    sid = (await api.create(api.StartSession(topic=previous), db_session, test_workspace))["id"]
    with using_language(opposite):
        prior = await ask(previous, sid=sid)
    requests.clear()
    answer = "Check repeated requirements." if english else "先检查多次出现的任职要求。"
    if path == "artifact_repair":
        block = {"type": "demo", "preset": "retry", "title": answer, "summary": answer}
        replies.extend([response("Draft without a block."), response(f"{answer}\n```study-blocks\n{json.dumps([block])}\n```")])
    else:
        replies.extend([httpx.Response(503 if path == "transport_failover" else 401), response(answer)])
    tasks = BackgroundTasks()
    with using_language(language):
        ticket = await api.send(sid, api.NewMessage(
            request_id="admitted-language", content=current,
            action="demo" if path == "artifact_repair" else "explain",
            mode="flash" if path == "manual_retry" else "auto",
        ), tasks, db_session, test_workspace)
    with using_language(opposite):
        await tasks()
        if path == "manual_retry":
            retry = BackgroundTasks()
            queued = await api.retry(sid, ticket["assistant_message_id"], retry, db_session, test_workspace)
            assert queued["attempt"] == 2
            await retry()
    reply = (await api.detail(sid, db_session, test_workspace))["messages"][-1]
    assert reply["status"] == "completed" and reply["content"] == answer
    assert len(requests) == 2 and not replies
    expected_retry = None if path == "manual_retry" else path
    assert [call["retry_kind"] for call in reply["trace"]["calls"]] == [None, expected_retry]
    for index, payload in enumerate(requests):
        assert payload["stream"] is True
        assert payload["max_tokens"] == reply["trace"]["calls"][index]["output_limit"]
        system, user = (message["content"] for message in payload["messages"])
        assert system.split("\n\n")[-1].startswith("Write the reply in English" if english else "回复统一使用简体中文")
        assert "repository_observation" in system and "study-blocks" in system
        repair = "The previous draft lacked a valid teaching artifact" if english else "前一版缺少可校验的教学产物"
        assert (repair in system) == (path == "artifact_repair" and index == 1)
        transcript, _, tail = user.partition("\n\n")
        wrapper, _, question = tail.partition("\n")
        assert wrapper.startswith("Answer the latest user message below in English" if english else "请用简体中文回答下面这条最新用户消息")
        assert question == current
        if english:
            assert not any("\u4e00" <= char <= "\u9fff" for char in system + wrapper)
        context = json.loads(transcript)
        assert context["target_role"] == "Synthetic QA engineer"
        assert context["materials"] == [source] and context["topic"] == previous
        assert [(turn["speaker"], turn["text"]) for turn in context["turns"]] == [
            ("user", previous), ("assistant", prior_reply),
        ]
        assert context["turns"][-1]["id"] == prior["id"]


@pytest.mark.asyncio
async def test_ordinary_questions_choose_roles_in_chinese_without_a_classifier(study, monkeypatch):
    ask, _ = study

    async def complete(_request):
        return response("合成教学答复。")

    requests = install(monkeypatch, None, handler=complete)
    cases = [
        ("explain", "什么是幂等和重试？", "tutor", "flash", "解决什么问题"),
        ("why", "为什么要比较两种确认策略的失败分支？", "tutor", "strong", "备选方案"),
        ("interview", "开始模拟面试", "interviewer", "flash", "一次只问一个"),
        ("critique", "点评我的回答：确认发生在保存之后。", "answer_reviewer", "flash", "没有原答"),
        ("jd", "根据岗位描述给我复习建议。", "jd_coach", "flash", "没有 JD"),
        ("project", "结合我的项目说说怎么实现。", "project_coach", "flash", "缺资料"),
    ]
    for action, question, role, tier, prompt_phrase in cases:
        reply = await ask(question, action=action)
        route = reply["trace"]
        assert reply["status"] == "completed", route.get("error_kind")
        assert (route["policy_version"], route["role"], route["tier"]) == ("study-auto-v2.2", role, tier)
        assert len(route["calls"]) == 1
        assert prompt_phrase in requests[-1]["messages"][0]["content"]
    assert [r["model"] for r in requests] == [
        "unchanged-model", "fixture-pro", "unchanged-model", "unchanged-model", "unchanged-model", "unchanged-model",
    ]
    if CONFIG["api_key"] in json.dumps(reply["trace"]):
        pytest.fail("Routing trace retained an authentication value", pytrace=False)


@pytest.mark.asyncio
async def test_applied_tasks_choose_depth_across_job_roles(study, monkeypatch, db_session, test_workspace):
    ask, _ = study

    async def complete(_request):
        return response("合成回复。")

    requests = install(monkeypatch, None, handler=complete)
    cases = [
        ("前端工程师", "报表要支持离线打开，同时保证只向有权限的用户缓存，给个方案。", "multiple_constraints"),
        ("数据工程师", "比较全量导入和增量导入，你会推荐哪种，分别有哪些风险？", "compare_alternatives"),
        ("测试开发工程师", "新版登录偶发失败但监控没告警，给我一个排查顺序。", "diagnose_failure"),
        ("后端工程师", "为电子发票设计回滚流程，写出关键步骤。", "design_solution"),
        ("门店主管", "新人这周只能上三个早班，老员工临时请假。该先调整谁的班次？", "scenario_decision"),
        ("采购", "One supplier can ship today but the batch is uninspected. What would you do first?", "scenario_decision"),
    ]
    for role, question, reason in cases:
        test_workspace.target_role = role
        await db_session.commit()
        reply = await ask(question)
        assert reply["status"] == "completed", reply["trace"].get("error_kind")
        assert reply["trace"]["tier"] == "strong"
        assert reason in reply["trace"]["reason_codes"]
        messages = requests[-1]["messages"]
        assert "会话的目标岗位" in messages[0]["content"]
        assert f'"target_role": "{role}"' in messages[1]["content"]
    definition = await ask("什么是架构选型？")
    assert definition["trace"]["tier"] == "flash"
    assert [r["model"] for r in requests] == ["fixture-pro"] * len(cases) + ["unchanged-model"]


@pytest.mark.asyncio
async def test_followup_inherits_depth_on_the_active_branch_and_manual_mode_wins(
    study, monkeypatch, db_session, test_workspace,
):
    ask, _ = study
    session = await api.create(api.StartSession(), db_session, test_workspace)
    sid = session["id"]

    async def complete(_request):
        return response("合成回复。")

    requests = install(monkeypatch, None, handler=complete)
    await ask("解释这两种确认策略各自的失败分支。", action="mechanism", sid=sid)
    followup = await ask("这一步为什么会重复？", sid=sid)
    assert followup["trace"]["tier"] == "strong"
    assert "grounded_followup" in followup["trace"]["reason_codes"]
    plain = await ask("刚才那段没听懂，换个白话例子。", sid=sid)
    assert plain["trace"]["action"] == "simplify"
    assert plain["trace"]["tier"] == "flash"
    manual = await ask("解释此前方案的失败分支。", action="mechanism", mode="flash", sid=sid)
    assert manual["trace"]["reason"] == "用户手动选择"
    assert manual["trace"]["tier"] == "flash"
    assert [call["model"] for call in requests] == ["fixture-pro", "fixture-pro", "unchanged-model", "unchanged-model"]


@pytest.mark.asyncio
async def test_project_route_uses_code_provenance_and_available_capability(
    study, monkeypatch, db_session, test_workspace,
):
    ask, settings = study
    project = Project(workspace_id=test_workspace.id, name="Synthetic queue", owner="", repo="", repo_url="")
    db_session.add(project)
    await db_session.commit()
    materials = [
        {"kind": "project_analysis", "id": "historical", "provenance": "project_report",
         "path": "old.py", "label": "历史报告", "text": "据称使用事务。"},
        {"kind": "scope_gap", "id": f"P{project.id}:stale-analysis", "provenance": "scope_limitation",
         "label": "版本缺口", "text": "报告版本已过期。"},
    ]

    async def supply(_session, turn, _timeout, _save):
        turn["sources"] = list(materials)

    monkeypatch.setattr(worker, "_gather_sources", supply)

    async def complete(_request):
        return response("先确认当前代码材料。")

    requests = install(monkeypatch, None, handler=complete)
    first = await ask("结合仓库说明持久化。", action="project", project_id=project.id)
    assert first["trace"]["evidence"] == {"status": "stale", "code_files": 0}
    assert first["trace"]["tier"] == "flash"
    materials.extend([
        {"kind": "project_code", "id": path, "provenance": "repository_observation", "revision": "synthetic",
         "path": path, "label": path, "text": "def commit(): pass"}
        for path in ("db.py", "worker.py")
    ])
    second = await ask("结合仓库说明持久化。", action="project", project_id=project.id)
    assert second["trace"]["tier"] == "strong"
    assert second["trace"]["evidence"] == {"status": "code_with_version_gap", "code_files": 2}
    assert "cross_file_evidence" in second["trace"]["reason_codes"]
    settings.study_strong_capabilities = ["text"]
    third = await ask("结合仓库说明持久化。", action="project", project_id=project.id)
    assert third["trace"]["profile_skips"] == {"strong": "capability"}
    assert third["trace"]["capability_gaps"] == ["code_reasoning"]
    assert [r["model"] for r in requests] == ["unchanged-model", "fixture-pro", "unchanged-model"]


@pytest.mark.asyncio
async def test_settings_db_default_and_explicit_models(study, monkeypatch, db_session):
    ask, settings = study
    settings.study_strong_model = ""
    monkeypatch.setattr(settings, "llm_model", "different-env-model")
    llm.set_llm_config_cache(None)
    setting = AppSetting(key="llm", value=seal_setting("llm", {**CONFIG, "model": "tenant-default"}))
    db_session.add(setting)
    await db_session.commit()
    try:
        async def complete(_request):
            return response("回答当前问题。")

        requests = install(monkeypatch, None, handler=complete)
        absent = await ask("解释失败分支中的状态取舍。", action="why")
        assert absent["status"] == "completed"
        assert absent["trace"]["preferred_tier"] == "strong"
        assert absent["trace"]["profile_skips"] == {"strong": "unavailable"}
        assert (await ask("解释。", mode="strong"))["trace"]["error_kind"] == "unavailable"

        settings.study_strong_model = "tenant-default"
        same = await ask("什么是消息确认？")
        assert same["trace"]["profile_skips"] == {"strong": "same_endpoint_and_model"}
        assert same["trace"]["max_calls"] == 1
        settings.study_flash_model = "explicit-flash"
        settings.study_flash_thinking = "disabled"
        manual_flash = (await ask("解释。", mode="flash"))["trace"]
        manual_strong = (await ask("解释。", mode="strong"))["trace"]
        assert manual_flash["tier"] == "flash" and manual_flash["calls"][0]["thinking_mode"] == "disabled"
        assert manual_strong["tier"] == "strong" and "thinking_mode" not in manual_strong["calls"][0]
        assert [r["model"] for r in requests] == [
            "tenant-default", "tenant-default", "explicit-flash", "tenant-default",
        ]
        assert [r.get("thinking") for r in requests] == [None, None, {"type": "disabled"}, None]
    finally:
        await db_session.delete(setting)
        await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure, expected_count, final_status, retained", [
    ("http_503", 2, "completed", "深度档合成回答"),
    ("http_401", 1, "failed", ""),
    ("blank_network", 2, "completed", "深度档合成回答"),
    ("whitespace_network", 2, "completed", "深度档合成回答"),
    ("hidden_block_network", 2, "completed", "深度档合成回答"),
    ("started_network", 1, "failed", "已发出的开头"),
    ("short_deadline", 1, "failed", ""),
    ("exhausted_budget", 1, "failed", ""),
    ("same_model", 1, "failed", ""),
    ("manual", 1, "failed", ""),
])
async def test_transport_failover_is_bounded_and_never_replaces_visible_answer(
    study, monkeypatch, failure, expected_count, final_status, retained,
):
    ask, settings = study
    if failure == "short_deadline":
        settings.study_timeout_seconds = 4
    if failure == "exhausted_budget":
        settings.study_output_budget = 6000
    if failure == "same_model":
        settings.study_strong_model = CONFIG["model"]
        settings.study_strong_provider = "different-label"
        settings.study_strong_base_url = "https://FIXTURE.INVALID:443/v1/"
        settings.study_strong_api_key = "synthetic-alternate-credential"

    async def transport(request):
        if json.loads(request.content)["model"] == "fixture-pro":
            return response("深度档合成回答")
        if failure in {"blank_network", "whitespace_network", "hidden_block_network", "started_network"}:
            chunks = {
                "blank_network": [],
                "whitespace_network": [frame(" \t\n\u3000")],
                "hidden_block_network": [frame("```study-blocks\n{}")],
                "started_network": [frame("已发出的开头")],
            }[failure]
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=Body(chunks, broken=True))
        return httpx.Response(401 if failure == "http_401" else 503)

    requests = install(monkeypatch, None, handler=transport)
    reply = await ask("什么是幂等？", mode="flash" if failure == "manual" else "auto")
    trace = reply["trace"]
    assert (reply["status"], reply["content"]) == (final_status, retained)
    assert len(trace["calls"]) == len(requests) == expected_count
    if expected_count == 2:
        assert trace["initial_tier"] == "flash"
        assert trace["tier"] == "strong"
        assert trace["calls"][1]["retry_kind"] == "transport_failover"
        assert sum(c["output_limit"] for c in trace["calls"]) <= settings.study_output_budget
    if failure == "started_network":
        assert trace["calls"][0]["fallback_blocked"] == "answer_started"
    if failure == "same_model":
        assert trace["profile_skips"] == {"strong": "same_endpoint_and_model"}


@pytest.mark.asyncio
async def test_generation_deadline_closes_a_stalled_attempt(study, monkeypatch):
    ask, settings = study
    settings.study_timeout_seconds = 3
    body = Body([frame("已返回的开头")], stall=True)
    requests = install(monkeypatch, body)
    reply = await ask("什么是幂等？")
    assert reply["status"] == "failed"
    assert reply["trace"]["error_kind"] == "timeout"
    assert reply["content"] == "已返回的开头"
    assert len(requests) == 1 and body.closed


@pytest.mark.asyncio
async def test_artifact_repair_keeps_first_draft_out_of_live_answer(study, monkeypatch):
    ask, settings = study
    settings.study_strong_model = ""
    snapshots = []
    monkeypatch.setattr(worker, "_publish_live", lambda _, snapshot: snapshots.append(snapshot))

    async def transport(request):
        system = json.loads(request.content)["messages"][0]["content"]
        if "前一版缺少可校验的教学产物" not in system:
            return response("不可见的首版。")
        return response('已校验的解释。\n```study-blocks\n'
                        '[{"type":"demo","preset":"retry","title":"重试","summary":"教学模拟"}]\n```')

    requests = install(monkeypatch, None, handler=transport)
    reply = await ask("做一个交互演示。", action="demo")
    assert reply["status"] == "completed"
    assert reply["content"] == "已校验的解释。"
    assert reply["blocks"][0]["type"] == "demo"
    assert [r["model"] for r in requests] == ["unchanged-model", "unchanged-model"]
    assert [call["retry_kind"] for call in reply["trace"]["calls"]] == [None, "artifact_repair"]
    assert all("不可见的首版" not in snapshot["content"] for snapshot in snapshots)


@pytest.mark.asyncio
async def test_optional_diagram_keeps_the_streaming_text_with_one_call(study, monkeypatch):
    ask, _ = study
    requests = install(monkeypatch, Body([frame("保留已经开始流式展示的文字。"), frame(finish="stop"), DONE]))
    question = "请完整讲清楚这三项服务的架构、交互、调用顺序和各自职责，再举一个有代表性的例子。"
    reply = await ask(question)
    assert reply["status"] == "completed"
    assert reply["content"] == "保留已经开始流式展示的文字。"
    assert reply["trace"]["diagram_result"] == "text_only"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_silent_first_attempt_leaves_time_for_the_alternate_tier(study, monkeypatch):
    ask, settings = study
    settings.study_timeout_seconds = 90
    settings.study_attempt_timeout_seconds = 20
    settings.study_first_content_timeout_seconds = 5.0

    async def provider(request):
        if json.loads(request.content)["model"] == "fixture-pro":
            return response("深度档合成回答")
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              stream=Body([b": heartbeat\r\n\r\n"], stall=True))

    requests = install(monkeypatch, None, handler=provider)
    reply = await ask("什么是幂等？", mode="auto")
    assert (reply["status"], reply["content"]) == ("completed", "深度档合成回答"), reply
    assert len(reply["trace"]["calls"]) == len(requests) == 2
    assert reply["trace"]["calls"][1]["retry_kind"] == "transport_failover"
    assert reply["trace"]["calls"][0]["error"] == "timeout"

pytestmark = pytest.mark.usefixtures("mock_private_billing")
