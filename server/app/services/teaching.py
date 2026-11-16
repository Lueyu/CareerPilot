"""教学任务内容生成(7 步通俗教学法)。

设计纪律(与项目原则一致):
- LLM 只做语义任务(讲解/类比/例子), 结构由确定性代码约束(严格 JSON Schema + 字段校验)。
- 材料来自题库同能力域题目的 answer_html/rubric(真实内容, 不编造)。
- LLM 未配置或调用失败 → 规则路径: 从题库材料确定性抽取, 诚实标记 source=rule。
- 内容缓存进 learning_tasks.content(生成一次, 二次读取瞬时), help 模式缓存进 content["help_cache"]。
"""
from __future__ import annotations

import html as _html
import json
import re as _re

TEACH_SYSTEM = """你是面向 AI 工程师的职业教练, 给学员生成一份「能照着学会」的教学任务内容。

教学纪律:
- 通俗优先: 先类比后原理, 类比要指明与真实机制的对应关系, 并说明类比在哪里失效。
- 只用「参考资料」里的真实知识, 不得编造术语、数字或机制; 材料不够就承认不够。
- 步骤必须可执行(有产出物), 每步 5-15 分钟。

输出严格 JSON, 字段缺一不可:
{
  "analogy": "<一个贯穿性类比, 120-220 字, 点明对应关系与失效处>",
  "example": "<一个具体小例子走一遍, 120-220 字, 含真实术语>",
  "principles": ["<核心原理/要点, 每条一句话, 3-5 条>"],
  "steps": ["<学习步骤, 每条具体可执行, 4-6 条>"],
  "verify": "<自我验证标准: 能独立讲清/写出什么才算掌握, 一句话>"
}"""

HELP_SYSTEM = """你是面向 AI 工程师的职业教练。学员正在完成一个学习任务, 对某一步有疑问, 点了求助按钮。

纪律: 用大白话; 只依据参考资料; 不编造; 直接回答, 不复述问题。
输出严格 JSON: {"text": "<120-260 字的回答>"}"""

HELP_MODES: dict[str, str] = {
    "simpler": "把当前这一步的内容用更简单的话重讲一遍(假设学员是刚入行的工程师)。",
    "analogy": "换一个全新的类比来解释当前知识点, 并点明与真实机制的对应关系。",
    "example": "换一个具体的小例子, 一步步走一遍当前知识点。",
    "project": "结合学员的项目背景(CareerPilot 项目: FastAPI+PostgreSQL+React, 多 Agent 流水线, GitHub 发布), 说明这个知识点在真实项目里怎么用。",
    "code": "给出一段 8-20 行的最小示意代码(伪代码或 Python), 帮助理解当前知识点, 并逐行注释。",
    "diagram": "用文字画一张 ASCII 流程图/结构图, 展示当前知识点的结构, 并配 2-3 句讲解。",
    "unclear": "学员还是不理解: 换一个更基础的切入角度(从问题/痛点出发, 而不是从概念出发)重讲一遍。",
}


def _strip_html(text: str, limit: int = 1200) -> str:
    if not text:
        return ""
    t = _re.sub(r"<[^>]+>", " ", text)
    t = _html.unescape(t)
    t = _re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def _parse_json_reply(raw) -> dict | None:
    """chat() 返回 {"content": "<json 字符串>"}——二次解析; 失败返回 None。"""
    if not isinstance(raw, dict):
        return None
    content = raw.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return None
    if not isinstance(content, dict):
        return None
    return content


async def _collect_material(session, skill_id: int | None, skill_key: str | None, limit: int = 3) -> str:
    """从题库收集同能力域的真实参考资料(题目答案解析)。"""
    from sqlalchemy import or_, select

    from app.models import Question

    if skill_id is None and not skill_key:
        return ""
    cond = []
    if skill_id is not None:
        cond.append(Question.skill_id == skill_id)
    if skill_key:
        cond.append(Question.topic == skill_key)
    rows = (
        await session.execute(
            select(Question)
            .where(or_(*cond))
            .order_by(Question.difficulty.asc())
            .limit(limit)
        )
    ).scalars().all()
    parts: list[str] = []
    for q in rows:
        ans = _strip_html(q.answer_html or "", 900)
        rub = q.rubric or {}
        kps = rub.get("key_points") or rub.get("points") or []
        kp_text = "；".join(
            (p.get("title") if isinstance(p, dict) else str(p)) for p in kps[:4]
        )
        seg = f"《{q.title[:60]}》"
        if kp_text:
            seg += f" 评分要点: {kp_text}"
        if ans:
            seg += f"\n{ans}"
        parts.append(seg)
    return "\n\n".join(parts)


def _rule_content(task, skill_name: str, material: str) -> dict:
    """规则路径: 从材料确定性抽取教学内容(诚实但结构完整)。"""
    lines = [ln.strip() for ln in material.split("\n") if ln.strip()]
    analogy = (
        f"「{skill_name}」可以类比成一条流水线：每个环节都要有输入、处理和验收，"
        "哪一环没有验收标准，问题就会流到下一环。这个类比的对应关系是——评分要点=验收标准，"
        "证据=产出物；失效之处在于流水线是线性的，而实际工程常常要回滚重做。"
        if material
        else f"「{skill_name}」可以类比成一条有验收标准的流水线：每个环节有输入、处理和验收。"
        "对应关系是评分要点=验收标准、证据=产出物；类比在线性流程上成立，遇到需要回滚迭代的场景会失效。"
    )
    example = (
        lines[0][:220] if lines else
        f"举个例子：在面试中被问到「{skill_name}」时，先用一句话给结论，再用 2-3 个机制细节支撑，"
        "最后主动说一个坑和应对办法——这个三段式本身就是可复用的例子。"
    )
    principles = [
        ln for ln in lines
        if 8 <= len(ln) <= 80 and ("：" in ln or "是" in ln)
    ][:4] or [
        f"先给结论再展开机制(面试与工作都适用的表达结构)",
        f"任何机制都要能说出一个真实坑与应对",
        f"知识要落到可验证的产出物上(代码/笔记/证据)",
    ]
    steps = [
        "阅读参考资料里最简单的一道题的答案解析(5 分钟)",
        "用自己的话把答案复述一遍, 写在任务笔记里(5 分钟)",
        "对照评分要点, 标出自己复述里缺失的部分(5 分钟)",
        "把缺失部分重新学习并补进笔记(10 分钟)",
        "把笔记要点登记为一条新证据(2 分钟)",
    ]
    verify = task.acceptance or "能脱稿讲清 3 个核心概念及适用边界"
    return {
        "analogy": analogy,
        "example": example,
        "principles": principles,
        "steps": steps,
        "verify": verify,
        "source": "rule",
    }


async def build_teaching_content(
    session,
    adapter,
    *,
    task,
    skill_name: str,
    skill_key: str | None,
) -> dict:
    """生成教学任务内容; 已生成的直接返回(缓存)。返回 dict 含 source: llm|rule。"""
    content = task.content or {}
    if content.get("analogy") and content.get("source") in ("llm", "rule"):
        return content

    material = await _collect_material(session, task.skill_id, skill_key)
    generated: dict | None = None
    if adapter.active:
        user = (
            f"任务: {task.title}\n"
            f"类型: {task.kind}(UNDERSTAND=理解/PRACTICE=实践/RETEST=复测)\n"
            f"能力域: {skill_name}\n"
            f"为什么学: {task.why or '能力差距'}\n"
            f"完成标准: {task.acceptance or '见 verify'}\n"
            f"参考资料(来自题库, 可能不完整):\n{material or '（该域暂无题库材料, 请用通用工程常识, 但不得编造具体数字）'}"
        )
        raw = await adapter.chat(TEACH_SYSTEM, user, json_mode=True, timeout=90.0)
        parsed = _parse_json_reply(raw)
        if parsed:
            analogy = str(parsed.get("analogy") or "").strip()
            example = str(parsed.get("example") or "").strip()
            principles = [str(p).strip() for p in (parsed.get("principles") or []) if str(p).strip()]
            steps = [str(s).strip() for s in (parsed.get("steps") or []) if str(s).strip()]
            verify = str(parsed.get("verify") or "").strip()
            if analogy and example and principles and steps and verify:
                generated = {
                    "analogy": analogy[:500],
                    "example": example[:500],
                    "principles": principles[:6],
                    "steps": steps[:8],
                    "verify": verify[:300],
                    "source": "llm",
                }

    if generated is None:
        generated = _rule_content(task, skill_name, material)
        generated["steps"] = [str(s) for s in (content.get("steps") or [])] or generated["steps"]
        if content.get("principles"):
            generated["principles"] = [str(p) for p in content.get("principles")][:6]

    # 保留模板里已有的 steps/principles 作兜底, 合并缓存
    merged = {**content, **generated}
    task.content = merged
    return merged


async def help_answer(
    session,
    adapter,
    *,
    task,
    skill_name: str,
    skill_key: str | None,
    mode: str,
) -> dict:
    """学习求助: 7 种模式。返回 {mode, text, source}。失败走规则降级。"""
    desc = HELP_MODES.get(mode)
    if not desc:
        return {"mode": mode, "text": None, "source": "none", "error": f"未知求助类型: {mode}"}

    content = task.content or {}
    cache = content.get("help_cache") or {}
    if isinstance(cache.get(mode), dict) and cache[mode].get("text"):
        return {"mode": mode, "text": cache[mode]["text"], "source": cache[mode].get("source", "llm")}

    material = await _collect_material(session, task.skill_id, skill_key, limit=2)
    step_hint = ""
    steps = content.get("steps") or []
    if steps:
        step_hint = f"当前学习步骤: {steps[0]}"

    text: str | None = None
    source = "rule"
    if adapter.active:
        user = (
            f"任务: {task.title}（能力域: {skill_name}）\n"
            f"{step_hint}\n"
            f"学员求助类型: {desc}\n"
            f"参考资料:\n{material or '（无题库材料, 用通用工程常识回答, 不得编造数字）'}"
        )
        raw = await adapter.chat(HELP_SYSTEM, user, json_mode=True, timeout=60.0)
        parsed = _parse_json_reply(raw)
        if parsed and str(parsed.get("text") or "").strip():
            text = str(parsed["text"]).strip()[:800]
            source = "llm"

    if text is None:
        # 规则降级: 从材料摘录 + 诚实说明
        excerpt = material.split("\n")[0][:160] if material else ""
        fallbacks = {
            "simpler": f"简单说：「{skill_name}」的核心就是——先给结论，再讲机制，最后说坑。{('参考资料要点：' + excerpt) if excerpt else ''}",
            "analogy": f"换个类比：「{skill_name}」像一条带验收标准的流水线，评分要点就是每个环节的验收单。{('参考资料要点：' + excerpt) if excerpt else ''}",
            "example": f"来个例子：面试被问到「{skill_name}」时，用结论→机制→坑的三段式回答。{(excerpt and '参考资料：' + excerpt) or ''}",
            "project": "结合本项目：CareerPilot 的导入流水线(解析→提取→映射→去重→生成)就是「机制+验收标准」的实例，每一步失败都有明确状态(FAILED)与恢复手段。",
            "code": "看代码思路：找出项目里对应这个知识点的模块，读它的输入/输出和错误处理分支，比读十篇文章更快。",
            "diagram": "画图思路：把「{s}」拆成 输入→处理→验收 三个框，在框之间标上失败时的走向，就得到一张可讲 1 分钟的图。".format(s=skill_name),
            "unclear": f"换个角度：先别想概念，想想「没有{skill_name}会发生什么」——任务会失败、没人知道为什么、无法复现。为了解决这些痛点，才有了这套机制。",
        }
        text = fallbacks.get(mode) or fallbacks["simpler"]

    # 缓存进 content.help_cache
    content = dict(content or {})
    hc = dict(content.get("help_cache") or {})
    hc[mode] = {"text": text, "source": source}
    content["help_cache"] = hc
    task.content = content
    return {"mode": mode, "text": text, "source": source}
