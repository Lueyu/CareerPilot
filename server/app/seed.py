"""CareerPilot 种子数据脚本。

用法(在 server/ 目录): python -m app.seed

幂等: 已存在的数据跳过。
内容:
1. 8 个能力域 Skill + JobStandard v1.0(种子权重/目标等级, PRD 表)
2. demo 工作空间(求职作战室) + owner 用户
3. 161 道公开题(从 ../web/src/data/bank.json 迁移, topic→skill 映射)
4. 示例证据 / 记忆 / trace(全部显式标记 DEMO)
5. 跑一遍 Competency Engine + Learning Planner, 让工作空间开箱即有画像与任务
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.db import SessionLocal, engine
from app.models import (
    Base, DecisionRecord, Evidence, Event, ImportSession, JobStandard,
    JobStandardTarget, MemoryItem, Question, Skill, SkillState, Trace,
    TraceSpan, User, Workspace, WorkspaceMember,
)
from app.services.competency import recompute_skill_states
from app.services.planner import plan_tasks_for_workspace
from app.services.tracing import add_span, finish_trace, start_trace

BANK_PATH = Path(__file__).resolve().parents[2] / "web" / "src" / "data" / "bank.json"

SKILLS = [
    ("agent", "Agent 架构与规划", "ReAct / 计划分解 / 多智能体编排 / harness 设计", 18, 4.0),
    ("tools", "工具调用与 MCP", "Function calling / MCP 协议 / 工具选择与组合", 14, 4.0),
    ("memory", "状态管理与 Memory", "短期记忆 / 长期记忆 / 状态一致性 / 上下文压缩", 12, 3.5),
    ("rag", "RAG 与上下文工程", "检索增强 / 上下文窗口管理 / prompt 工程", 10, 3.5),
    ("eval", "Agent Evaluation", "评测设计 / 离线在线评估 / benchmark / 回归防护", 16, 4.5),
    ("backend", "后端与系统设计", "API 设计 / 并发 / 数据库 / 算法与复杂度", 12, 3.5),
    ("reliability", "可靠性与安全", "失败处理 / 重试 / 幻觉与注入防护 / 权限边界", 12, 3.5),
    ("deploy", "部署与可观测性", "部署 / 监控 / 日志 / 成本观测", 6, 3.0),
]

# 旧题库 topic → 新能力域 key
TOPIC_SKILL = {
    "rag": "rag",
    "harness": "agent",
    "ctx": "rag",
    "infer": "backend",
    "eval": "eval",
    "sys": "reliability",
    "algo": "backend",
    "biz": "tools",
}

# DEMO 证据: (skill_key, kind, title, grade, support)
DEMO_EVIDENCE = [
    ("agent", "PROJECT_COMMIT", "Squabble: 实现多轮对抗的 agent 编排循环", "VERIFIED", 0.5),
    ("agent", "MEMORY_NOTE", "自述熟悉 ReAct 模式但未做过失败降级", "UNVERIFIED_CLAIM", 0.1),
    ("eval", "EXAM_ANSWER", "专项考试: 设计离线评测集, 部分命中要点", "MEDIUM", 0.2),
    ("eval", "PROJECT_COMMIT", "Squabble: 为裁判 agent 建了 60 条用例的回归集", "VERIFIED", 0.6),
    ("tools", "PROJECT_COMMIT", "Squabble: 工具调用层做了幂等与超时控制", "HIGH", 0.4),
    ("rag", "MEMORY_NOTE", "自述: RAG 项目做过切片与混合检索调参", "MEDIUM", 0.3),
    ("backend", "EXAM_ANSWER", "专项考试: 高并发 RAG 容量估算, 答出主要步骤", "MEDIUM", 0.2),
    ("reliability", "MEMORY_NOTE", "自述: 对 prompt 注入防护只有理论了解", "LOW", -0.1),
    ("deploy", "PROJECT_COMMIT", "个人网站: CI 自动构建 + OSS 部署", "HIGH", 0.3),
]

DEMO_MEMORIES = [
    ("CareerGoal", "目标: 2026 年内拿到 AI Agent 应用开发工程师 P5+ offer", "ACTIVE"),
    ("KnowledgeGapSignal", "自述对 Agent 评测体系不熟, 只有零散实践", "ACTIVE"),
    ("ProjectDecision", "Squabble 选择自建裁判 agent 而非第三方评测 API, 为了可控性", "ACTIVE"),
]


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        # ---------- 1) 能力域 + 岗位标准 ----------
        skills: dict[str, Skill] = {s.key: s for s in (await session.execute(select(Skill))).scalars().all()}
        if not skills:
            for key, name, desc, _w, _t in SKILLS:
                s = Skill(key=key, name=name, description=desc, dimensions=["理论理解", "工程实践", "问题诊断", "表达沟通", "AI协作"])
                session.add(s)
                skills[key] = s
            await session.flush()
            print(f"[seed] skills: {len(SKILLS)} created")

        standard = (
            await session.execute(select(JobStandard).where(JobStandard.is_seed.is_(True)))
        ).scalar_one_or_none()
        if standard is None:
            standard = JobStandard(
                role="AI Agent 应用开发工程师",
                level="P5",
                version="v1.0",
                jd_sample_count=3,
                is_seed=True,
            )
            session.add(standard)
            await session.flush()
            for key, _n, _d, weight, target in SKILLS:
                session.add(
                    JobStandardTarget(
                        standard_id=standard.id, skill_id=skills[key].id,
                        weight_pct=weight, target_level=target,
                    )
                )
            await session.flush()
            print("[seed] JobStandard v1.0 (seed) created with 8 targets")

        # ---------- 2) demo 工作空间 ----------
        ws = (
            await session.execute(select(Workspace).where(Workspace.slug == "demo"))
        ).scalar_one_or_none()
        if ws is None:
            ws = Workspace(slug="demo", name="求职作战室", is_demo=True)
            session.add(ws)
            await session.flush()
            print("[seed] workspace 'demo' (求职作战室) created")
        user = (
            await session.execute(select(User).where(User.email == "demo@careerpilot.icu"))
        ).scalar_one_or_none()
        if user is None:
            user = User(email="demo@careerpilot.icu", display_name="Lueyu")
            session.add(user)
            await session.flush()
        exists_member = (
            await session.execute(
                select(func.count()).select_from(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id
                )
            )
        ).scalar()
        if not exists_member:
            session.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
        await session.flush()

        # ---------- 3) 161 道公开题 ----------
        n_q = (await session.execute(select(func.count(Question.id)))).scalar() or 0
        if n_q == 0 and BANK_PATH.exists():
            bank = json.loads(BANK_PATH.read_text(encoding="utf-8"))
            for q in bank["questions"]:
                skill_key = TOPIC_SKILL.get(q["topic"], "agent")
                tags = q.get("tags") or []
                kw_tokens = (q.get("kw") or "").split()
                points = [
                    {"title": f"要点: {tag}", "keywords": [tag] + kw_tokens[:2]}
                    for tag in tags[:3]
                ] or [{"title": "要点: 核心概念", "keywords": kw_tokens[:3] or [q["title"][:6]]}]
                session.add(
                    Question(
                        short_code=q["id"],
                        workspace_id=None,  # 公共题库
                        title=q["title"],
                        qtype="open",
                        topic=q["topic"],
                        skill_id=skills[skill_key].id,
                        difficulty=int(q.get("difficulty") or 3),
                        options=None,
                        answer_html=q.get("html") or "",
                        rubric={"points": points, "max_points": 5.0, "advice": "按要点覆盖度评分"},
                        company=q.get("company"),
                        source_type="PUBLIC_LICENSED",
                        tags=tags,
                        keywords=kw_tokens,
                        is_demo=False,
                    )
                )
            await session.flush()
            print(f"[seed] questions: {len(bank['questions'])} imported from bank.json")
        else:
            print(f"[seed] questions: skip ({n_q} already exist)")

        # ---------- 4) DEMO 证据 / 记忆 / trace ----------
        n_ev = (
            await session.execute(
                select(func.count(Evidence.id)).where(Evidence.workspace_id == ws.id)
            )
        ).scalar() or 0
        if n_ev == 0:
            for i, (key, kind, title, grade, support) in enumerate(DEMO_EVIDENCE, start=1):
                session.add(
                    Evidence(
                        short_code=f"EV-0{i:02d}",
                        workspace_id=ws.id,
                        skill_id=skills[key].id,
                        kind=kind,
                        title=f"[DEMO] {title}",
                        detail="演示数据 · 非真实评估",
                        grade=grade,
                        support=support,
                        source_ref="seed:demo",
                    )
                )
            print(f"[seed] evidence: {len(DEMO_EVIDENCE)} demo items")

        n_mem = (
            await session.execute(
                select(func.count(MemoryItem.id)).where(MemoryItem.workspace_id == ws.id)
            )
        ).scalar() or 0
        if n_mem == 0:
            for mtype, content, status in DEMO_MEMORIES:
                session.add(
                    MemoryItem(
                        workspace_id=ws.id, mtype=mtype, content=f"[DEMO] {content}",
                        source="SYSTEM", extract_confidence=0.95, status=status,
                    )
                )
            print(f"[seed] memories: {len(DEMO_MEMORIES)} demo items")

        n_tr = (
            await session.execute(select(func.count(Trace.id)).where(Trace.workspace_id == ws.id))
        ).scalar() or 0
        if n_tr == 0:
            # 成功的导入 trace
            t1 = await start_trace(session, ws.id, "bank_import", "[DEMO] 面经导入: 字节真题.pdf", "TR-101")
            add_span(session, t1, "parse", 420, 0.01, detail={"pages": 3})
            add_span(session, t1, "extract", 860, 0.03, detail={"questions": 12})
            add_span(session, t1, "dedup", 150, 0.0, detail={"duplicates": 2})
            await finish_trace(session, t1, "SUCCESS", 1430, 0.04)
            # 失败的项目扫描 trace
            t2 = await start_trace(session, ws.id, "project_scan", "[DEMO] 项目扫描: lueyu.github.io", "TR-102")
            add_span(session, t2, "clone", 900, 0.0, detail={"repo": "lueyu.github.io"})
            span = add_span(session, t2, "extract_evidence", 260, 0.0, detail={"error": "git diff timeout"}, status="ERROR")
            await finish_trace(session, t2, "FAILED", 1160, 0.0, fail_reason="git diff timeout")
            session.add(
                DecisionRecord(
                    short_code="DC-101", workspace_id=ws.id, trace_id=t1.id,
                    question="导入的 2 道重复题如何处理?",
                    chosen="合并为 canonical 的变体(保留原题)",
                    alternatives=["丢弃新题", "保留两题独立"],
                    evidence_refs=["IM-101"],
                    rationale="PRD: 去重保留原题+variants, 便于溯源同题不同问法",
                )
            )
            await session.flush()
            print("[seed] traces: 2 demo (1 SUCCESS import, 1 FAILED scan) + 1 decision")

        # ---------- 5) 引擎跑一遍 ----------
        states = await recompute_skill_states(session, ws.id)
        tasks = await plan_tasks_for_workspace(session, ws.id)
        await session.commit()
        print(f"[seed] SkillStates: {len(states)} domains, {sum(1 for s in states if s.estimated_level is not None)} estimated")
        print(f"[seed] LearningTasks: {len(tasks)} main tasks (with retest chains)")
        print("[seed] done.")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(seed())
