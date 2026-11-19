"""确定性引擎测试 — 纯函数单元 + 少量 DB 集成(evolution 规则)。

运行: cd server && python -m pytest tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

# ---------------------------------------------------------------- benchmark_jd
from app.services.benchmark_jd import parse_jd, build_targets

SKILLS = [
    {"key": "agent", "name": "Agent 架构与规划"},
    {"key": "tools", "name": "工具调用与 MCP"},
    {"key": "memory", "name": "状态管理与 Memory"},
    {"key": "rag", "name": "RAG 与上下文工程"},
    {"key": "eval", "name": "Agent Evaluation"},
    {"key": "backend", "name": "后端与系统设计"},
    {"key": "reliability", "name": "可靠性与安全"},
    {"key": "deploy", "name": "部署与可观测性"},
]

JD_TEXT = """
岗位职责:
1. 负责 AI Agent 平台的架构设计与多步规划调度;
2. 建设 MCP 工具接入规范与工具调用网关;
3. 搭建 RAG 检索增强链路, 优化上下文工程;
4. 建立 Agent 评测集与 LLM 裁判评分体系;
5. 设计高并发 FastAPI 后端服务与缓存;
6. 保障线上服务稳定性与 prompt 注入防护;
7. 负责灰度发布与模型可观测性建设;
8. 构建长期记忆与多轮对话状态管理。
任职要求: 本科及以上学历, 3 年以上后端经验。
"""


def test_parse_jd_skill_hits():
    parsed = parse_jd(JD_TEXT, SKILLS)
    assert set(parsed.keys()) >= {"requirements", "keywords", "skill_hits", "hard_requirements"}
    hits = {h["skill_key"]: h["hits"] for h in parsed["skill_hits"]}
    # 8 个域全部命中(文本里每域都有关键词)
    assert len(hits) == 8
    assert all(v >= 1 for v in hits.values())


def test_parse_jd_hard_requirements():
    parsed = parse_jd(JD_TEXT, SKILLS)
    assert any("本科" in r for r in parsed["hard_requirements"])
    assert any("3 年" in r for r in parsed["hard_requirements"])


def _base_targets():
    return [
        {"skill_key": "eval", "skill_name": "Agent Evaluation", "skill_id": 5, "weight_pct": 16, "target_level": 5.0},
        {"skill_key": "deploy", "skill_name": "部署与可观测性", "skill_id": 8, "weight_pct": 6, "target_level": 3.5},
        {"skill_key": "backend", "skill_name": "后端与系统设计", "skill_id": 6, "weight_pct": 12, "target_level": 4.5},
    ]


def test_build_targets_raises_never_lowers_and_caps():
    parsed = parse_jd(JD_TEXT, SKILLS)
    new, changes = build_targets(_base_targets(), parsed)
    assert all(t["target_level"] <= 5.0 for t in new)          # 上限 5.0
    assert all(c["to"] >= c["from"] for c in changes)          # 永不下调
    m = {c["skill_key"]: c for c in changes}
    assert m["backend"]["to"] == 4.75 and m["backend"]["from"] == 4.5   # hits 5 ≥3 <6 → +0.25
    assert "deploy" not in m            # hits 2 <3 → 不上调
    # eval 已 5.0 封顶 → 不出现在 changes 或 to==5.0
    assert "eval" not in m or m["eval"]["to"] == 5.0


def test_build_targets_no_hits_no_change():
    parsed = parse_jd("完全无关的职位描述: 会计与审计。", SKILLS)
    new, changes = build_targets(_base_targets(), parsed)
    assert changes == []
    assert new[0]["target_level"] == 5.0


# ---------------------------------------------------------------- scorer(规则评分)
from app.models import Question
from app.services.scorer import score_mcq, score_open, score_question, build_feedback


def _open_q():
    return Question(
        id=1, qtype="open",
        rubric={
            "max_points": 5.0,
            "advice": "先结论后机制",
            "points": [
                {"title": "保证幂等", "keywords": ["幂等"], "detail": "重试不产生副作用"},
                {"title": "重试策略", "keywords": ["重试", "退避"], "detail": "指数退避+上限"},
            ],
        },
    )


def _mcq_q():
    return Question(id=2, qtype="mcq", rubric={"answer_index": 1, "max_points": 5.0, "points": []})


def test_score_mcq_correct_wrong():
    r = score_mcq(_mcq_q(), 1)
    assert r["verdict"] == "CORRECT" and r["points"] == 5.0
    r2 = score_mcq(_mcq_q(), 0)
    assert r2["verdict"] == "WRONG" and r2["points"] == 0.0
    r3 = score_mcq(_mcq_q(), None)     # 未作答
    assert r3["verdict"] == "WRONG"


def test_score_open_keyword_coverage():
    full = score_open(_open_q(), "要保证幂等, 失败后按重试+退避策略")
    assert full["verdict"] == "CORRECT" and full["points"] == 5.0
    assert len(full["hits"]) == 2 and full["misses"] == []
    none = score_open(_open_q(), "什么都不答")
    assert none["verdict"] == "WRONG" and none["points"] == 0.0
    empty = score_open(_open_q(), None)
    assert empty["verdict"] == "WRONG"


def test_score_question_dispatch():
    assert score_question(_mcq_q(), choice_index=1, text=None)["verdict"] == "CORRECT"
    assert score_question(_open_q(), choice_index=None, text="幂等 重试")["verdict"] == "CORRECT"


def test_build_feedback_structure():
    result = score_open(_open_q(), "只提到幂等")
    fb = build_feedback(_open_q(), result)
    assert fb["hit_points"][0]["title"] == "保证幂等"
    assert fb["missed_points"][0]["title"] == "重试策略"
    assert fb["advice"] == "先结论后机制"
    assert fb["next"] == "重试策略"          # next = 第一个缺失要点


# ---------------------------------------------------------------- followup
from app.services.followup import suggest_followups


def test_followups_at_most_two_and_schema():
    missed = ["缺幂等机制", "没有重试上限"]
    fus = suggest_followups("设计一个安全的重试机制", "PARTIAL", missed)
    assert 1 <= len(fus) <= 2
    for f in fus:
        assert {"question", "kind", "label", "why"} <= set(f.keys())
        assert f["question"] and f["why"]


def test_followups_wrong_without_missed_is_blank():
    fus = suggest_followups("设计重试机制", "WRONG", [])
    assert fus and all(f["question"] for f in fus)


# ---------------------------------------------------------------- llm_judge
from app.services.llm_judge import judge_open_answer


class FakeAdapter:
    active = True

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def chat(self, system, user, json_mode=False, timeout=60.0):
        self.calls += 1
        import json as _j
        return {"content": _j.dumps(self.payload, ensure_ascii=False)}


RUBRIC = {
    "max_points": 5.0,
    "points": [
        {"title": "保证幂等", "detail": "重试不产生副作用"},
        {"title": "重试策略", "detail": "指数退避"},
    ],
}


@pytest.mark.asyncio
async def test_judge_verdict_deterministic_not_trusting_llm():
    # verdict 由 points/max 比例确定性计算, 不存在 LLM 自报 verdict 字段
    fake = FakeAdapter({"points": 2.0, "reasoning": "只命中幂等要点", "hit": ["保证幂等"], "missing": ["重试策略"]})
    out = await judge_open_answer(fake, title="设计重试", skill_name="工具调用", rubric=RUBRIC, text="我的作答")
    assert out is not None
    assert out["verdict"] == "PARTIAL" and out["points"] == 2.0 and out["max_points"] == 5.0
    assert fake.calls == 1

    fake2 = FakeAdapter({"points": 0.5, "reasoning": "答非所问", "hit": [], "missing": ["保证幂等", "重试策略"]})
    out2 = await judge_open_answer(fake2, title="设计重试", skill_name="工具调用", rubric=RUBRIC, text="答非所问")
    assert out2["verdict"] == "WRONG"


@pytest.mark.asyncio
async def test_judge_empty_text_short_circuits():
    fake = FakeAdapter({})
    assert await judge_open_answer(fake, title="t", skill_name="s", rubric=RUBRIC, text="  ") is None
    assert fake.calls == 0        # 空作答不浪费 LLM 调用


@pytest.mark.asyncio
async def test_judge_bad_json_and_no_reasoning_fail_closed():
    class Bad(FakeAdapter):
        async def chat(self, *a, **k):
            return {"content": "not-json{{"}

    assert await judge_open_answer(Bad({}), title="t", skill_name="s", rubric=RUBRIC, text="x") is None

    class NoReason(FakeAdapter):
        async def chat(self, *a, **k):
            import json as _j
            return {"content": _j.dumps({"points": 5.0})}   # 缺 reasoning → 拒绝

    assert await judge_open_answer(NoReason({}), title="t", skill_name="s", rubric=RUBRIC, text="x") is None


# ---------------------------------------------------------------- teaching
from app.services.teaching import _parse_json_reply, _rule_content, HELP_MODES
from app.models import LearningTask


def test_parse_json_reply_handles_chat_shape():
    import json as _j
    payload = {"analogy": "像备份", "example": "e", "principles": ["p"], "steps": ["s"], "verify": "v"}
    assert _parse_json_reply({"content": _j.dumps(payload)}) == payload
    assert _parse_json_reply({"content": "bad json"}) is None
    assert _parse_json_reply(None) is None


def test_rule_content_never_empty():
    task = LearningTask(title="实现最小评测", acceptance="跑通一次评测并记录指标")
    c = _rule_content(task, "Agent Evaluation", "材料1: 评测集设计\n材料2: LLM 裁判")
    assert c["source"] == "rule"
    assert c["analogy"] and c["steps"] and c["verify"]


def test_help_modes_defined():
    assert {"simpler", "analogy", "example", "project", "code", "diagram", "unclear"} <= set(HELP_MODES)


# ---------------------------------------------------------------- content_validation
from app.services.content_validation import run_all, privacy_scan, is_low_risk


def test_privacy_scan_flags_phone_and_email():
    assert privacy_scan("联系我 13800138000 或 a@b.com").status == "FAIL"
    assert privacy_scan("正常内容没有隐私").status == "PASS"


def test_run_all_low_risk_gate():
    ok = run_all("# 标题\n\n正文内容, 无隐私无量化声明。", sources=[{"license_status": "OWNED"}])
    assert ok.status in ("PASS", "WARN")
    bad = run_all("拨打 13800138000", sources=[{"license_status": "RESTRICTED"}])
    assert bad.status == "FAIL"
    assert is_low_risk(ok) is True and is_low_risk(bad) is False


# ---------------------------------------------------------------- evolution 规则(DB 集成, monkeypatch 信号)
from app.services import evolution as evo


def _sig(avg, rt=0.0, rtt=5, exams=5):
    return {"completed_exams": exams, "avg_score_ratio": avg, "retest_total": rtt,
            "retest_done": int(rtt * rt), "retest_rate": rt, "task_total": 10,
            "task_done": 5, "task_rate": 0.5}


@pytest.mark.asyncio
async def test_evolution_propose_rules(monkeypatch, db_session, test_workspace):
    signals = _sig(0.4)

    async def fake_sig(session, wid):
        return signals

    monkeypatch.setattr(evo, "collect_signals", fake_sig)
    ws_id = test_workspace.id

    low = await evo.propose_candidate(db_session, ws_id)
    assert low["proposed"] and low["candidate"].body["candidate"]["difficulty_bias"] == 1
    await evo.reject_candidate(db_session, ws_id, low["candidate"])

    signals = _sig(0.9)
    high = await evo.propose_candidate(db_session, ws_id)
    assert high["proposed"] and high["candidate"].body["candidate"]["difficulty_bias"] == -1
    await evo.reject_candidate(db_session, ws_id, high["candidate"])

    signals = _sig(0.7, rt=0.3)
    cadence = await evo.propose_candidate(db_session, ws_id)
    assert cadence["proposed"] and cadence["candidate"].kind == "retest_cadence"
    await evo.reject_candidate(db_session, ws_id, cadence["candidate"])

    signals = _sig(0.72, rt=0.7)
    healthy = await evo.propose_candidate(db_session, ws_id)
    assert healthy["proposed"] is False and "暂无" in healthy["reason"]

    signals = _sig(0.5, exams=2)
    few = await evo.propose_candidate(db_session, ws_id)
    assert few["proposed"] is False and "样本不足" in few["reason"]
