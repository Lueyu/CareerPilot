"""The two generation channels share a strict, model-free import contract."""
import copy
import importlib
import importlib.util
import json

import pytest


def protocol():
    assert importlib.util.find_spec("app.services.question_generation") is not None, "generation protocol must exist"
    return importlib.import_module("app.services.question_generation")


@pytest.fixture
def snapshot():
    return {
        "kind": "supplement", "count": 1, "skill_keys": ["rag"],
        "skills": [{"key": "rag", "name": "检索增强生成"}],
        "project_context": [{
            "id": 5, "name": "资料检索", "kind": "manual",
            "profile": {"role": "后端工程师", "responsibilities": "实现召回评估"},
            "has_personal_context": True, "snapshot_fingerprint": "abc",
            "facts": [{"id": "P5-role", "text": "后端工程师", "source": "user_statement"},
                      {"id": "P5-responsibilities", "text": "实现召回评估", "source": "user_statement"}],
        }],
        "source_text": "如何评估召回？", "source_questions": [{"id": 9, "title": "召回如何评估？", "canonical_question_id": None}],
        "purpose": "补充召回评估练习",
    }


@pytest.fixture
def item():
    return {
        "title": "如何评估 RAG 召回质量？", "original_question": "如何评估召回？",
        "skill_key": "rag", "question_mode": "general", "project_id": None, "fact_refs": [],
        "answer": {
            "tldr": "用标注相关集合计算召回指标。", "elevator": "先冻结查询和相关文档集合，再比较召回。",
            "analogy": "像按采购单找物品；相似性不是相关性的完整替代。", "examiner": ["评估集合与指标"],
            "steps": [{"title": "固定分母", "detail": "为每条查询标注相关文档，并说明不完整标注的偏差。"}],
            "how_to_answer": "先讲标注集合，再解释 Recall@k，最后说明误差。",
            "pitfalls": ["只看一个查询会掩盖长尾。"],
            "followups": [{"q": "如何选 k？", "a": "结合延迟与召回曲线，评估增量收益。"}],
            "key_points": [{"title": "相关集合", "detail": "交代标注方法和分母，不混淆召回与准确率。"}],
            "terms": [{"term": "Recall@k", "desc": "前 k 个结果覆盖相关文档的比例。"}],
        },
        "project_answer": "", "hypothetical_answer": "", "missing_context": [], "source_question_id": 9,
    }


def response(item, batch_id=17):
    return json.dumps({"schema_version": 1, "batch_id": batch_id, "items": [item]}, ensure_ascii=False)


def test_whole_chat_reply_preserves_structured_answer_without_llm(snapshot, item, monkeypatch):
    p = protocol()
    monkeypatch.setattr(p, "AgentAdapter", lambda: pytest.fail("external parsing must not invoke a model"))
    parsed = p.parse_response("下面是生成结果：\n```json\n" + response(item) + "\n```\n请审核后使用。", snapshot, 17)
    assert parsed == [item]
    rendered = p.render_item(parsed[0], snapshot)
    assert item["answer"]["followups"][0]["a"] in rendered["answer_html"]
    assert rendered["rubric"]["points"][0]["detail"] == item["answer"]["key_points"][0]["detail"]


def test_prompt_contains_snapshot_contract_and_explicit_fact_boundaries(snapshot):
    p = protocol()
    snapshot["api_key"] = "SHOULD_NOT_APPEAR"
    prompt = p.build_prompt(snapshot, 17)
    for needed in ("batch_id", "17", "P5-role", "source_question_id", "needs_context", "key_points", "不代表本人", "通用"):
        assert needed in prompt
    assert "SHOULD_NOT_APPEAR" not in prompt
    assert "如何评估召回" in prompt


def test_project_answer_requires_selected_project_and_frozen_facts(snapshot, item):
    p = protocol()
    item.update(question_mode="project", project_id=5, fact_refs=["P5-role", "P5-responsibilities"], project_answer="我负责召回评估；实现细节需进一步补充。")
    parsed = p.parse_response(response(item), snapshot, 17)
    assert parsed[0]["fact_refs"] == item["fact_refs"]
    rendered = p.render_item(parsed[0], snapshot)
    assert "后端工程师" in rendered["answer_html"]
    assert "我负责召回评估" in rendered["answer_html"]


def test_personal_answer_requires_user_statement_among_actual_references(snapshot, item):
    snapshot["project_context"][0]["facts"].append({
        "id": "P5-finding-1", "text": "仓库包含评估脚本", "source": "repository_observation",
    })
    item.update(question_mode="project", project_id=5, fact_refs=["P5-finding-1"], project_answer="我实现了评估脚本。")
    with pytest.raises(ValueError, match="user_statement"):
        protocol().parse_response(response(item), snapshot, 17)
    item["fact_refs"].append("P5-responsibilities")
    assert protocol().parse_response(response(item), snapshot, 17)[0] == item


def test_project_source_labels_are_readable_without_changing_metadata(snapshot, item):
    snapshot["project_context"][0]["facts"].append({
        "id": "P5-finding-1", "text": "仓库包含评估脚本", "source": "repository_observation",
    })
    item.update(question_mode="project", project_id=5, fact_refs=["P5-finding-1", "P5-responsibilities"], project_answer="我实现了召回评估。")
    original = copy.deepcopy(snapshot)
    rendered = protocol().render_item(item, snapshot)["answer_html"]
    assert "用户陈述" in rendered
    assert "仓库观察" in rendered
    assert "user_statement" not in rendered
    assert "repository_observation" not in rendered
    assert snapshot == original


@pytest.mark.parametrize("change,expected", [
    ({"skill_key": "secret"}, "skill_key"),
    ({"source_question_id": 999}, "source_question_id"),
    ({"question_mode": "project", "project_id": 99}, "project_id"),
    ({"question_mode": "project", "project_id": 5, "fact_refs": ["P99-role"], "project_answer": "真实项目"}, "fact_refs"),
    ({"question_mode": "project", "project_id": 5, "fact_refs": [], "project_answer": "真实项目"}, "fact_refs"),
    ({"question_mode": "general", "project_id": 5}, "general"),
    ({"question_mode": "general", "project_answer": "我把延迟降低了 80%"}, "general"),
    ({"question_mode": "needs_context", "missing_context": []}, "missing_context"),
    ({"question_mode": "needs_context", "missing_context": ["个人职责"], "project_answer": "我完成了"}, "project_answer"),
    ({"question_mode": "unrecognized"}, "question_mode"),
    ({"company": "虚构大厂真实面试"}, "company"),
])
def test_rejects_invalid_references_and_modes(snapshot, item, change, expected):
    item.update(change)
    with pytest.raises(ValueError, match=expected):
        protocol().parse_response(response(item), snapshot, 17)


def test_repository_observations_cannot_become_personal_project_answer(snapshot, item):
    snapshot["project_context"][0]["has_personal_context"] = False
    snapshot["project_context"][0]["profile"] = {}
    item.update(question_mode="project", project_id=5, fact_refs=["P5-role"], project_answer="我负责实现召回。")
    with pytest.raises(ValueError, match="needs_context"):
        protocol().parse_response(response(item), snapshot, 17)
    item.update(question_mode="needs_context", project_answer="", missing_context=["你的实际职责"], hypothetical_answer="如果由我设计，会先建立评估集。")
    assert protocol().parse_response(response(item), snapshot, 17)[0]["question_mode"] == "needs_context"


def test_missing_context_without_a_project_is_a_reviewable_draft(snapshot, item):
    snapshot["project_context"] = []
    item.update(question_mode="needs_context", missing_context=["请先选择实际参与的项目"], hypothetical_answer="如果需要设计，可先按通用方案讨论。")
    assert protocol().parse_response(response(item), snapshot, 17)[0] == item


@pytest.mark.parametrize("mutate,expected", [
    (lambda x: x["answer"].update(followups=[{"q": "为什么"}]), "followups"),
    (lambda x: x["answer"].update(key_points=[]), "key_points"),
    (lambda x: x["answer"].update(tldr=""), "tldr"),
    (lambda x: x["answer"].update(steps="not an array"), "steps"),
    (lambda x: x.update(fact_refs="P5-role"), "fact_refs"),
    (lambda x: x.update(project_id=True), "project_id"),
    (lambda x: x["answer"].update(unknown_metadata="lost"), "unknown_metadata"),
])
def test_bad_shapes_never_silently_lose_content(snapshot, item, mutate, expected):
    mutate(item)
    with pytest.raises(ValueError, match=expected):
        protocol().parse_response(response(item), snapshot, 17)


def test_batch_identity_count_version_and_ambiguous_reply_rejected(snapshot, item):
    p = protocol()
    with pytest.raises(ValueError, match="batch_id"):
        p.parse_response(response(item, 18), snapshot, 17)
    with pytest.raises(ValueError, match="schema_version"):
        p.parse_response(response(item).replace('"schema_version": 1', '"schema_version": 2'), snapshot, 17)
    with pytest.raises(ValueError, match="题量"):
        p.parse_response(response(item), {**snapshot, "count": 2}, 17)
    assert len(p.parse_response(response(item), {**snapshot, "kind": "import", "count": 2}, 17)) == 1
    with pytest.raises(ValueError, match="多个"):
        p.parse_response("```json\n" + response(item) + "\n```\n```json\n" + response(item) + "\n```", snapshot, 17)
    with pytest.raises(ValueError, match="JSON"):
        p.parse_response("this is not JSON", snapshot, 17)


def test_duplicate_titles_in_one_batch_do_not_create_artificial_variety(snapshot, item):
    duplicate = copy.deepcopy(item)
    duplicate["title"] = "  " + duplicate["title"] + "  "
    raw = json.dumps({"schema_version": 1, "batch_id": 17, "items": [item, duplicate]}, ensure_ascii=False)
    with pytest.raises(ValueError, match="重复"):
        protocol().parse_response(raw, {**snapshot, "count": 2}, 17)


def test_renderer_escapes_model_and_project_text(snapshot, item):
    item["answer"]["tldr"] = '<script>alert("x")</script>'
    item["original_question"] = "<img src=x onerror=evil()>"
    rendered = protocol().render_item(item, snapshot)
    assert "<script>" not in rendered["answer_html"]
    assert "&lt;script&gt;" in rendered["answer_html"]
    assert "<img" not in rendered["answer_html"]
    assert "规则引擎生成" not in rendered["answer_html"]


@pytest.mark.asyncio
async def test_api_generation_uses_effective_config_and_keeps_usage(monkeypatch):
    p = protocol()
    calls = []

    class Adapter:
        async def _effective(self):
            calls.append("config")
            return {"provider": "configured", "model": "chosen-model", "api_key": "secret", "base_url": "https://example.invalid"}

        async def chat_with(self, cfg, system, user, **kwargs):
            calls.append("call")
            assert cfg["model"] == "chosen-model"
            assert kwargs["json_mode"] is True
            return {"content": '{"schema_version":1}', "usage": {"total_tokens": 123}}

    monkeypatch.setattr(p, "AgentAdapter", Adapter)
    result = await p.generate_response("prompt")
    assert calls == ["config", "call"]
    assert result["text"] == '{"schema_version":1}'
    assert result["model"] == "chosen-model"
    assert result["usage"] == {"total_tokens": 123}
    assert "secret" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [False, True])
async def test_api_unavailable_is_failure_without_rule_fallback(monkeypatch, configured):
    p = protocol()

    class Adapter:
        async def _effective(self):
            return {"provider": "configured", "api_key": "secret", "base_url": "https://example.invalid"} if configured else {}

        async def chat_with(self, *args, **kwargs):
            return None

    monkeypatch.setattr(p, "AgentAdapter", Adapter)
    with pytest.raises(RuntimeError):
        await p.generate_response("prompt")


@pytest.mark.asyncio
async def test_api_exception_does_not_persist_provider_credentials(monkeypatch):
    p = protocol()

    class Adapter:
        async def _effective(self):
            return {"provider": "configured", "api_key": "secret-key", "base_url": "https://example.invalid"}

        async def chat_with(self, *args, **kwargs):
            raise RuntimeError("request included Authorization: Bearer secret-key")

    monkeypatch.setattr(p, "AgentAdapter", Adapter)
    with pytest.raises(RuntimeError) as error:
        await p.generate_response("prompt")
    assert "secret-key" not in str(error.value)
