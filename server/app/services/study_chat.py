import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, literal, select, update
from sqlalchemy.dialects.postgresql import JSONB, array
from sqlalchemy.exc import SQLAlchemyError

from app.config import MAX_STUDY_TIMEOUT_SECONDS, get_settings
from app.db import SessionLocal
from app.generation_i18n import generation_message, saved_language
from app.i18n import Locale, message as localized_message, using_language
from app.models.jd import JdSource
from app.models.study_chat import StudyMessage, StudySession
from app.services.llm import AgentAdapter, get_llm_config
from app.services.study_routing import (
    POLICY_VERSION,
    STREAM_CAPABILITIES,
    transient_failure,
    tutoring_intent,
    tutoring_profiles,
)

# 项目证据检索是单轮生成里的子任务, 给它独立但不苛刻的预算,
# 避免项目代码扫描吃掉整轮生成时限。
PROJECT_EVIDENCE_TIMEOUT_SECONDS = 120

GENERATION_LEASE_SECONDS = 30
GENERATION_HEARTBEAT_SECONDS = 10

# SSE 流式推送: 数据库快照轮询间隔与心跳帧周期(秒)。
STREAM_POLL_SECONDS = 0.5
STREAM_HEARTBEAT_SECONDS = 15.0
# 生成任务把 reasoning/content 增量推给同一事件循环里的 SSE 订阅者,
# 只有订阅者存在时才推送; 数据库仍是持久化与断线恢复的唯一真相。
STREAM_LIVE_PUSH_SECONDS = 0.03
_STREAM_BLOCK_FENCE = re.compile(r"(?:^|\n)```study-blocks\s*(?:\n|$)")

@dataclass
class _StreamChannel:
    listeners: set[asyncio.Event] = field(default_factory=set)
    worker_attempt: int | None = None
    latest: dict | None = None


_STREAMS: dict[int, _StreamChannel] = {}


def _release_channel(message_id, channel):
    if channel.worker_attempt is None and not channel.listeners:
        _STREAMS.pop(message_id, None)


def _publish_live(message_id, snapshot):
    channel = _STREAMS.get(message_id)
    if not channel:
        return
    previous = channel.latest
    attempt = snapshot["attempt"]
    if previous and (attempt < previous["attempt"] or (
        attempt == previous["attempt"] and previous["status"] != "generating"
    )):
        return
    if channel.worker_attempt is not None and attempt < channel.worker_attempt:
        return
    channel.latest = snapshot
    for listener in channel.listeners:
        listener.set()


def interrupt_content(row):
    """Keep the latest same-attempt draft when interrupting on the generating worker."""
    channel = _STREAMS.get(row.id)
    latest = channel.latest if channel else None
    if (latest and latest["attempt"] == row.attempt and latest["stage"] == "generating"
            and latest["model"] == row.model and latest["content"].startswith(row.content)):
        return latest["content"]
    return row.content


def lease_deadline():
    return datetime.now(timezone.utc) + timedelta(seconds=GENERATION_LEASE_SECONDS)


def expired_reply_error():
    # Recovery can expire several turns with different saved languages at once.
    return case((StudyMessage.coach["response_language"].as_string() == "en",
                 localized_message("study.error.expired", "en")),
                else_=localized_message("study.error.expired", "zh-CN"))


async def expire_generations(session, session_id=None):
    query = update(StudyMessage).where(
        StudyMessage.status == "generating", StudyMessage.expires_at <= func.now())
    if session_id is not None:
        query = query.where(StudyMessage.session_id == session_id)
    return await session.execute(query.values(
        status="interrupted", error=expired_reply_error(), expires_at=None))


async def recover_interrupted():
    async with SessionLocal() as session:
        await expire_generations(session)
        await session.commit()


def render_conversation(context, messages, sources):
    return json.dumps({
        "topic": context["topic"], "target_role": context.get("target_role"),
        "materials": sources, "turns": [
            {"id": row.id, "speaker": row.role, "text": row.content[:8000]} for row in messages
        ],
    }, ensure_ascii=False)


def fit_request(data, question, instruction, capacity, *, language: Locale = "zh-CN"):
    """Keep the newest bounded context and source IDs when an ongoing chat grows."""
    tail = f"\n\n{generation_message('study_chat.current_turn', language)}\n{question}"
    material = dict(data)
    turns = list(material.get("turns", []))
    material["turns"] = turns
    limit = max(0, capacity - len(instruction.encode()) - 1500)
    def render():
        return json.dumps(material, ensure_ascii=False) + tail
    result = render()
    while len(result.encode()) > limit and len(turns) > 2:
        old = turns.pop(0)
        summaries = material.setdefault("earlier_excerpts", [])
        summaries.append({"id":old["id"],"speaker":old["speaker"],"excerpt":old["text"][:180]})
        material["earlier_excerpts"] = summaries[-12:]
        result = render()
    if len(result.encode()) > limit:
        material["turns"] = [{**t,"text":t["text"][:1400]} for t in turns]
        material["earlier_excerpts"] = (material.get("earlier_excerpts") or [])[-4:]
        result = render()
    return result


def teaching_action(action, question):
    if action != "explain":
        return action
    for pattern, selected in (
        (r"交互(网页|演示)|可调参数|interactive (?:demo|simulation)", "demo"), (r"补强|项目改进|improve my project", "improve"),
        (r"结合.{0,60}项目|我的项目|my project|careerpilot.{0,40}(项目|代码|实现|架构|讲解)", "project"),
        (r"画(一张|个|一下)?图|时序图|架构图|状态图|(?:draw|show|create) (?:me )?(?:a |an )?(?:(?:sequence|architecture|state) )?diagram", "diagram"),
        (r"模拟面试|面试官追问|mock interview|interview me", "interview"), (r"点评.{0,6}回答|(?:review|critique) my answer", "critique"),
        (r"岗位描述|根据.{0,10}JD|job description", "jd"), (r"没(听|看)懂|simplify|simpler terms", "simplify"),
    ):
        if re.search(pattern, question, re.I):
            return selected
    if re.search(r"为什么|为何|设计理由|取舍|\bwhy\b", question, re.I):
        return "why"
    if re.search(r"怎么(工作|实现|运行)|如何(工作|实现|运行)|机制|调用链|执行流程|\bhow does\b", question, re.I):
        return "mechanism"
    return action


async def publish(message_id, attempt, **values):
    stage = (values.get("coach") or {}).get("trace", {}).get("stage")
    terminal = values.get("status") in {"completed", "failed", "interrupted"}
    notify = terminal or "model" in values or stage in {
        "repairing", "escalating",
    }
    if terminal and "coach" in values:
        values["coach"] = func.jsonb_set(
            literal(values["coach"], type_=JSONB),
            array(["trace", "terminal_write_at"]),
            func.to_jsonb(func.statement_timestamp()),
        )
    statement = update(StudyMessage).where(
        StudyMessage.id == message_id, StudyMessage.attempt == attempt,
        StudyMessage.status == "generating", StudyMessage.expires_at > func.now(),
    ).values(**values)
    if notify:
        statement = statement.returning(StudyMessage.id, StudyMessage.status, StudyMessage.content,
                                        StudyMessage.model, StudyMessage.attempt, StudyMessage.error)
    async with SessionLocal() as session:
        result = await session.execute(statement)
        row = result.one_or_none() if notify else None
        saved = row is not None if notify else bool(result.rowcount)
        await session.commit()
    if row is not None:
        _publish_live(message_id, {**row._mapping, "stage": stage or row.status, "reasoning": None})
    return saved


async def branch_history(session, reply):
    """Follow saved edges. Legacy messages before migration have linear ancestry."""
    query = select(StudyMessage).where(StudyMessage.session_id == reply.session_id,
        StudyMessage.project_id.is_not_distinct_from(reply.project_id), StudyMessage.status == "completed")
    previous = reply
    history = []
    for _ in range(24):
        meta = previous.coach or {}
        if "parent_message_id" in meta:
            if not meta["parent_message_id"]:
                break
            step = query.where(StudyMessage.id == meta["parent_message_id"])
        else:
            step = query.order_by(StudyMessage.id.desc())
        previous = await session.scalar(step.where(StudyMessage.id < previous.id).limit(1))
        if previous is None:
            break
        history.append(previous)
    return history


async def _load_turn(session, message_id, attempt, coach, trace):
    """Load the live reply and its conversation branch. None means no live turn."""
    reply = await session.scalar(select(StudyMessage).where(StudyMessage.id == message_id,
        StudyMessage.status == "generating", StudyMessage.attempt == attempt))
    if reply is None:
        return None
    coach.update(reply.coach or {})
    previous_trace = coach.get("trace") or {}
    trace["calls"] = previous_trace.get("calls", [])[-20:]
    if "library_retrieval" in previous_trace:
        trace["library_retrieval"] = previous_trace["library_retrieval"]
    coach["trace"] = trace
    row = await session.get(StudySession, reply.session_id)
    history = await branch_history(session, reply)
    question = history[0].content
    action = teaching_action(reply.action, question)
    prior = next((m for m in history[1:] if m.role == "assistant"), None)
    if prior and reply.action == "explain" and action == "explain" and (
        (prior.coach or {}).get("trace", {}).get("action") == "interview"
        and not re.search(r"结束|暂停|直接讲|解释|不练|stop|pause|explain", question, re.I)
    ):
        action = "interview"
    sources = [s for s in reply.sources or [] if not (s["kind"].startswith("project_") or s["kind"] in ("scope_gap","jd"))]
    return {"message_id": message_id, "attempt": attempt, "reply": reply, "row": row,
            "history": history, "question": question, "action": action, "prior": prior,
            "project_id": reply.project_id, "sources": sources, "coach": coach}


async def _gather_sources(session, turn, timeout, save):
    """Collect project code evidence and the latest JD excerpt into turn['sources']."""
    from app.services.study_project_evidence import collect_project_evidence
    question = turn["question"]
    action = turn["action"]
    project_id = turn["project_id"]
    row = turn["row"]
    history = turn["history"]
    prior = turn["prior"]
    sources = turn["sources"]
    project_task = action in ("project", "improve") or (project_id and (action in ("mechanism","diagram","why") or re.search(r"项目|代码|文件|调用链|project|source code|file|call chain", question, re.I)))
    if project_task:
        await save("retrieving")
        subject = question
        if len(question) < 24:
            previous_questions = [m.content[:200] for m in history[1:5] if m.role == "user"]
            subject = " ".join([question, *previous_questions[:2]])
        if project_id:
            found = await asyncio.wait_for(collect_project_evidence(session, row.workspace_id,
                row.context.get("skill_ids", []), project_id, subject),
                timeout=min(PROJECT_EVIDENCE_TIMEOUT_SECONDS, timeout))
            turn["sources"] = [s for s in sources if not s["kind"].startswith("project_")] + found[:16]
        else:
            sources.append({"kind":"project_gap", "id":0, "label":localized_message("project.missing_label"),
                "text":localized_message("project.select"),"provenance":"scope"})
    elif prior:
        # Carry the exact previous citations when continuing in the same project.
        sources.extend(s for s in prior.sources or [] if (s["kind"].startswith("project_") or s["kind"]=="scope_gap") and
                       project_id and s not in sources)
    sources = turn["sources"]
    if action == "jd":
        jd = await session.scalar(select(JdSource).where(JdSource.workspace_id == row.workspace_id)
            .order_by(JdSource.created_at.desc(), JdSource.id.desc()).limit(1))
        if jd:
            sources.append({"kind":"jd", "id":jd.id,"label":jd.title,"text":jd.raw_text[:3500],
                            "date":jd.created_at.isoformat(),"provenance":"imported_jd"})


def _compose_request(turn, trace, language: Locale):
    """Route the turn and build the model prompt; fills turn with the request pieces."""
    from app.services.interview_workflow import ROLE_INSTRUCTIONS
    from app.services.study_blocks import TEXT_ONLY, block_prompt, wants_teaching_diagram
    from app.services.study_teaching import teaching_prompt
    action = turn["action"]
    question = turn["question"]
    history = turn["history"]
    project_id = turn["project_id"]
    sources = turn["sources"]
    coach = turn["coach"]
    reply = turn["reply"]
    row = turn["row"]
    mode = coach.get("mode", "auto")
    context = dict(row.context)
    if (reply.project_id is not None and not any(m.role == "assistant" for m in history)) or reply.project_id != row.context.get("project_id") or any(
        m.project_id != reply.project_id for m in history):
        context["topic"] = question
    recent = list(reversed(history[:12]))
    request_data = json.loads(render_conversation(context, recent, sources))
    if history[12:]:
        request_data["earlier_excerpts"] = [{"id":m.id,"speaker":m.role,"excerpt":m.content[:200]}
            for m in reversed(history[12:])]
    # Put the current turn at the very end. The session title and copied
    # conversation are context, never the request to answer again.
    request_data["turns"] = request_data["turns"][:-1]
    instruction = "\n".join((
        teaching_prompt(action, language),
        generation_message("study_chat.context_contract", language),
        block_prompt(action, question, sources, language=language),
    ))
    agent_role = coach.get("agent_role")
    if agent_role in ROLE_INSTRUCTIONS:
        instruction += "\n" + ROLE_INSTRUCTIONS[agent_role]
        instruction += (
            "\nProvide a substantive written response before or alongside any structured block. "
            "Use Markdown paragraphs, headings or lists as needed. "
            "Put each table row on its own line, with matching columns. "
            "Keep follow-up suggestions as prose or list items. Reserve code fences for actual code "
            "and the explicitly requested study-blocks format."
        )
        trace["agent_role"] = agent_role
        workflow = context.get("interview_workflow")
        if workflow and workflow["status"] != "completed":
            request_data["interview_workflow"] = {
                "status": workflow["status"],
                "current_step": workflow["steps"][workflow["current"]],
                "remaining_steps": workflow["steps"][workflow["current"] + 1:],
            }
            instruction += "\nIf a workflow is paused, do not move it forward. Never claim that chat text changed workflow state."
    proactive_diagram = wants_teaching_diagram(action, question)
    if proactive_diagram:
        instruction += "\n" + localized_message("blocks.proactive", language)
    artifact = action if action in ("diagram", "demo") and not TEXT_ONLY.search(question) else None
    intent = tutoring_intent(action, question, turn["prior"], sources, artifact, proactive_diagram, project_id)
    trace.update(
        policy_version=POLICY_VERSION, action=action, mode=mode, project_id=project_id,
        message_ids=[m.id for m in reversed(history[:12])],
        preferred_tier=intent.preferred_tier if mode == "auto" else mode,
        role=intent.role, reason_codes=[code for code, _ in intent.reasons],
        reason=localized_message("route.separator").join(reason for _, reason in intent.reasons) if mode == "auto" else localized_message("route.manual"),
        evidence={"status": intent.evidence, "code_files": intent.code_files},
        required_capabilities=sorted(STREAM_CAPABILITIES), preferred_capabilities=sorted(intent.specialties),
        max_calls=0,
    )
    trace["diagram_intent"] = "proactive" if proactive_diagram else "explicit" if artifact == "diagram" else None
    turn.update(mode=mode, intent=intent, artifact=artifact, request_data=request_data,
                instruction=instruction, proactive_diagram=proactive_diagram)


async def _generate(turn, settings, deadline, trace, save, fail, language: Locale):
    """Run the selected profile and at most one pre-publication retry."""
    from app.services.study_blocks import TEXT_ONLY, extract_blocks, teaching_diagram_fallback
    timing = trace["latency"]
    base = await asyncio.wait_for(get_llm_config(), max(0, deadline - time.monotonic()))
    budget = max(0, min(settings.study_output_budget, 32000))
    mode = turn["mode"]
    intent = turn["intent"]
    artifact = turn["artifact"]
    question = turn["question"]
    sources = turn["sources"]
    prior = turn["prior"]
    coach = turn["coach"]
    request_data = turn["request_data"]
    output_language = generation_message("study_chat.output_language", language)
    instruction = f"{turn['instruction']}\n\n{output_language}"
    proactive_diagram = turn["proactive_diagram"]
    message_id = turn["message_id"]
    attempt = turn["attempt"]
    profiles, skipped = tutoring_profiles(base, settings, mode, intent)
    trace.update(profile_skips=skipped, max_output_tokens=budget)

    def fit(candidate, system, remaining_budget):
        # Each candidate starts with the same branch context. Fitting a profile
        # may append an excerpt when it moves an older turn out of the prompt.
        data = dict(request_data)
        if "earlier_excerpts" in data:
            data["earlier_excerpts"] = list(data["earlier_excerpts"])
        request = fit_request(data, question, system, candidate.context, language=language)
        # UTF-8 bytes provide a conservative upper bound for these text inputs.
        available = candidate.context - len((system + request).encode("utf-8")) - 64
        return request, min(remaining_budget, available, candidate.output_tokens)

    options = []
    for candidate in profiles:
        request, allowance = fit(candidate, instruction, budget)
        if allowance >= 128:
            options.append((candidate, request, allowance))
        else:
            skipped[candidate.tier] = "budget"
    if not options:
        timing["routing_finished_ms"] = round((time.monotonic() - turn["submitted_at"]) * 1000, 2)
        if profiles:
            await fail("budget", localized_message("study.error.budget"))
        elif "capability" in skipped.values():
            await fail("capability", localized_message("study.error.capability"))
        else:
            await fail("unavailable", localized_message("study.error.unavailable"))
        return

    selected, request, allowance = options[0]
    alternate = next((candidate for candidate, _, _ in options[1:]
                      if candidate.identity != selected.identity and not (
                          settings.study_relay_same_upstream and candidate.config["model"] == selected.config["model"]
                      )), None)
    if len(options) > 1 and alternate is None:
        skipped[options[1][0].tier] = "same_endpoint_and_model"
    preferred = trace["preferred_tier"]
    if selected.tier != preferred:
        explanation = {
            "unavailable": "偏好档位未配置",
            "capability": "偏好档位缺少文本流式能力",
            "budget": "偏好档位无法容纳本轮请求",
        }.get(skipped.get(preferred), "所选档位更符合本轮能力需求")
        trace["reason"] += localized_message("route.separator") + explanation

    async def stream_attempt(adapter, cfg, model, instruction, request, allowance, call, remaining):
        fragments = []
        reasoning_parts = []
        started = time.monotonic()
        last_publish = last_push = float("-inf")

        def live_snapshot():
            return {"id": message_id, "status": "generating", "content": "" if artifact else "".join(fragments),
                    "model": model, "attempt": attempt, "error": None, "stage": "generating",
                    "reasoning": "".join(reasoning_parts) or None}

        def push_live(now):
            nonlocal last_push
            if now - last_push >= STREAM_LIVE_PUSH_SECONDS:
                _publish_live(message_id, live_snapshot())
                last_push = now

        async def partial(text):
            nonlocal last_publish, last_push
            fragments.append(text)
            if artifact:
                return
            now = time.monotonic()
            first_body = False
            if "first_visible_body_ms" not in call and text.strip():
                body = "".join(fragments)
                fence = _STREAM_BLOCK_FENCE.search(body)
                first_body = bool((body[:fence.start()] if fence else body).strip())
            if first_body:
                call["first_visible_body_ms"] = round((now - started) * 1000, 2)
                timing = trace["latency"]
                timing.setdefault("first_visible_body_ms", round((now - turn["submitted_at"]) * 1000, 2))
                timing.setdefault("body_release_stage", "stream")
                _publish_live(message_id, live_snapshot())
                last_push = now
            else:
                push_live(now)
            if first_body or ("first_visible_body_ms" in call and now - last_publish >= STREAM_POLL_SECONDS):
                await save("generating", content="".join(fragments))
                last_publish = now

        async def thinking(text):
            # Reasoning 只用于实时展示, 不落库、不进 trace, 进程重启后由数据库最终状态收敛。
            reasoning_parts.append(text)
            push_live(time.monotonic())

        call["provider_start_ms"] = round((started - turn["submitted_at"]) * 1000, 2)
        trace["latency"].setdefault("provider_start_ms", call["provider_start_ms"])
        operation = asyncio.create_task(adapter.chat_with({**cfg, "output_limit":allowance, "report_usage":True},
            instruction, request, stream=True, on_content=partial, on_reasoning=thinking,
            timeout=remaining, total_timeout=remaining))
        try:
            async with asyncio.timeout(remaining):
                while not operation.done():
                    done, _ = await asyncio.wait({operation}, timeout=1.0)
                    if not done:
                        async with SessionLocal() as checking:
                            active = await checking.scalar(select(StudyMessage.id).where(StudyMessage.id == message_id,
                                StudyMessage.status == "generating", StudyMessage.attempt == attempt,
                                StudyMessage.expires_at > func.now()))
                        if not active:
                            raise InterruptedError
                result = operation.result()
        except TimeoutError:
            adapter.last_error = {"kind": "timeout"}
            result = None
        finally:
            ended = time.monotonic()
            call["elapsed_ms"] = int((ended - started) * 1000)
            call["provider_end_ms"] = round((ended - turn["submitted_at"]) * 1000, 2)
            if not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
        return result, fragments

    max_calls = 2 if mode == "auto" and (alternate or artifact) else 1
    trace.update(initial_tier=selected.tier, max_calls=max_calls)
    timing["routing_finished_ms"] = round((time.monotonic() - turn["submitted_at"]) * 1000, 2)
    repair_draft = None
    retry_kind = None
    for index in range(max_calls):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        cfg = selected.config
        model = str(cfg["model"])[:200]
        missing = sorted(intent.specialties - selected.capabilities)
        adapter = AgentAdapter()
        call = dict(provider=cfg["provider"], requested_model=model, reported_model=None,
                    usage=None, cost=None, outcome="unknown", attempt=attempt, generation=index + 1,
                    escalation=int(bool(index) and selected.tier == "strong" and trace["initial_tier"] == "flash"),
                    elapsed_ms=None, error=None, tier=selected.tier,
                    retry_kind=retry_kind, output_limit=allowance, capability_gaps=missing)
        if "thinking" in cfg:
            call["thinking_mode"] = cfg["thinking"]
        trace.update(tier=selected.tier, capabilities=sorted(selected.capabilities), capability_gaps=missing)
        if missing and not index and mode == "auto":
            trace["reason"] += "；配置中缺少本轮偏好的专长，使用可用档位尝试"
        trace["calls"].append(call)
        await save("generating", model=model, content="")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        result, fragments = await stream_attempt(
            adapter, cfg, model, instruction, request, allowance, call, remaining,
        )
        call.update(usage=adapter.last_usage,
                    reported_model=adapter.last_response_meta.get("reported_model"),
                    first_reasoning_ms=adapter.last_response_meta.get("first_reasoning_ms"),
                    first_token_ms=adapter.last_response_meta.get("first_content_ms"),
                    first_nonwhitespace_content_ms=adapter.last_response_meta.get("first_nonwhitespace_content_ms"))
        if result is not None:
            call["outcome"] = "completed"
        used = (adapter.last_usage or {}).get("completion_tokens")
        budget -= used if type(used) is int and used >= 0 else allowance
        can_retry = index == 0 and max_calls == 2 and budget >= 256 and deadline - time.monotonic() >= 5
        if result and result.get("content", "").strip():
            content, blocks = extract_blocks(result["content"], sources)
            if TEXT_ONLY.search(question):
                blocks = []
            if proactive_diagram and not any(block['type'] == 'diagram' for block in blocks):
                fallback = teaching_diagram_fallback(question, language)
                if fallback:
                    fallback['id'] = f'b{min(len(blocks), 2) + 1}'
                    blocks = [*blocks[:2], fallback]
                    trace['diagram_result'] = 'teaching_fallback'
                else:
                    trace["diagram_result"] = "text_only"
            if artifact and not any(block["type"] == artifact for block in blocks):
                call["error"] = "invalid_artifact"
                repair_draft = content or result["content"]
                if can_retry:
                    repair = generation_message("study_chat.artifact_repair", language)
                    repair_instruction = f"{turn['instruction']}\n{repair}\n\n{output_language}"
                    next_request, next_allowance = fit(selected, repair_instruction, budget)
                    if next_allowance >= 256:
                        call["saved_output"] = repair_draft[:16000]
                        instruction, request, allowance = repair_instruction, next_request, next_allowance
                        retry_kind = "artifact_repair"
                        trace["reason"] += "；首版教学产物未通过校验，尝试修复一次"
                        await save("repairing")
                        continue
                trace["error_kind"] = "invalid_artifact"
                await save("failed", status="failed", content=repair_draft,
                           error=localized_message("study.error.diagram"), expires_at=None)
                return
            for block in blocks:
                block["version_message_id"] = message_id
                block["parent_message_id"] = coach.get("revision_of") or (prior.id if prior else None)
            coach["blocks"] = blocks
            await save("completed", status="completed", content=content or result["content"],
                       error=None, expires_at=None)
            return
        error = adapter.last_error or {"kind": "invalid_response"}
        call["error"] = error["kind"]
        if "status" in error:
            call["http_status"] = error["status"]
        if error["kind"] == "configuration" or (
            error["kind"] == "http" and 400 <= error["status"] < 500 and error["status"] != 408
        ):
            call["outcome"] = "failed"
        streamed_answer = "first_visible_body_ms" in call
        if streamed_answer:
            call["fallback_blocked"] = "answer_started"
        elif can_retry and alternate and transient_failure(error, adapter.last_response_meta.get("stream_error")):
            next_request, next_allowance = fit(alternate, instruction, budget)
            if next_allowance >= 256:
                selected, request, allowance = alternate, next_request, next_allowance
                retry_kind = "transport_failover"
                trace["reason"] += "；首档未完成回复，切换到另一可用档位"
                await save("escalating")
                continue
        trace["error_kind"] = call["error"]
        await save("failed", status="failed", content=repair_draft or ("".join(fragments) if streamed_answer else ""),
                   error=localized_message("study.error.partial").format(error=call["error"]), expires_at=None)
        return


async def _keep_generation_lease(message_id: int, attempt: int, stop_at: float):
    """Renew only this attempt, including when the provider is quiet."""
    while (started := time.monotonic()) < stop_at:
        remaining = stop_at - started
        extension = timedelta(seconds=min(remaining, GENERATION_LEASE_SECONDS))
        try:
            async with asyncio.timeout(min(remaining, GENERATION_HEARTBEAT_SECONDS)):
                if not await publish(message_id, attempt, expires_at=func.statement_timestamp() + extension):
                    return
        except (SQLAlchemyError, TimeoutError) as exc:
            logging.getLogger(__name__).warning(
                "study lease renewal failed for message %s attempt %s (%s)", message_id, attempt, type(exc).__name__)
        await asyncio.sleep(max(0, started + GENERATION_HEARTBEAT_SECONDS - time.monotonic()))


async def generate_reply(message_id: int, attempt: int, *, submitted_at=None, accepted_at=None):
    worker_started_at = time.monotonic()
    submitted_at = worker_started_at if submitted_at is None else submitted_at

    def since_submit(now):
        return round((now - submitted_at) * 1000, 2)

    settings = get_settings()
    timeout = max(1, min(settings.study_timeout_seconds, MAX_STUDY_TIMEOUT_SECONDS))
    deadline = time.monotonic() + timeout
    timing = {"origin": "message_request" if accepted_at is not None else "worker_start",
              "worker_start_ms": since_submit(worker_started_at)}
    if accepted_at is not None:
        timing["accepted_ms"] = since_submit(accepted_at)
    trace = {"stage": "starting", "calls": [], "error_kind": None, "latency": timing}
    coach = {"trace": trace}
    channel = None

    async def save(stage, **values):
        trace["stage"] = stage
        if values.get("status") in {"completed", "failed", "interrupted"}:
            timing["finalizing_ms"] = since_submit(time.monotonic())
            if (values.get("content") or "").strip() and "first_visible_body_ms" not in timing:
                timing["first_visible_body_ms"] = timing["finalizing_ms"]
                timing["body_release_stage"] = "terminal"
        if not await publish(message_id, attempt, coach=coach, **values):
            raise InterruptedError

    async def fail(kind, message):
        trace["error_kind"] = kind
        await save("failed", status="failed", error=message, expires_at=None)

    heartbeat = asyncio.create_task(_keep_generation_lease(message_id, attempt, deadline + GENERATION_LEASE_SECONDS))
    try:
        async with SessionLocal() as session:
            turn = await _load_turn(session, message_id, attempt, coach, trace)
            if turn is None:
                return
            channel = _STREAMS.setdefault(message_id, _StreamChannel())
            if channel.worker_attempt is None or attempt >= channel.worker_attempt:
                channel.worker_attempt = attempt
            turn["submitted_at"] = submitted_at
            language = saved_language(coach.get("response_language"))
            with using_language(language):
                await _gather_sources(session, turn, timeout, save)
                _compose_request(turn, trace, language)
            timing["context_ready_ms"] = since_submit(time.monotonic())
            timing["routing_started_ms"] = timing["context_ready_ms"]
            await save("routing", sources=turn["sources"])
        with using_language(language):
            await _generate(turn, settings, deadline, trace, save, fail, language)
    except InterruptedError:
        return
    except asyncio.CancelledError:
        trace["error_kind"] = "cancelled"
        try:
            await save("interrupted", status="interrupted", error=localized_message("study.error.interrupted", coach.get("response_language", "zh-CN")), expires_at=None)
        except InterruptedError:
            pass
        raise
    except Exception as exc:
        logging.getLogger(__name__).warning("study generation %s failed during %s (%s)", message_id, trace["stage"], type(exc).__name__)
        trace["error_kind"] = "timeout" if isinstance(exc, TimeoutError) else "tool" if trace["stage"] == "retrieving" else "generation"
        if trace["stage"] == "routing" and "routing_started_ms" in timing:
            timing.setdefault("routing_finished_ms", since_submit(time.monotonic()))
        try:
            await save("failed", status="failed", error=localized_message("study.error.failed", coach.get("response_language", "zh-CN")), expires_at=None)
        except InterruptedError:
            pass
    finally:
        heartbeat.cancel()
        if channel and channel.worker_attempt == attempt:
            channel.worker_attempt = None
            if channel.latest and channel.latest["attempt"] == attempt and channel.latest["status"] == "generating":
                channel.latest = None
                for listener in channel.listeners:
                    listener.set()
            _release_channel(message_id, channel)
        await asyncio.gather(heartbeat, return_exceptions=True)


def stream_snapshot(row):
    trace = (row.coach or {}).get("trace") or {}
    return {
        "id": row.id,
        "status": row.status,
        "content": row.content or "",
        "model": row.model,
        "attempt": row.attempt,
        "error": row.error,
        "stage": trace.get("stage") if row.status == "generating" else row.status,
        "reasoning": None,
    }


def notify_stream(row):
    _publish_live(row.id, stream_snapshot(row))


async def expire_message_if_stale(message_id):
    """Mirror expire_generations for a single message; returns True when it flipped."""
    async with SessionLocal() as session:
        result = await session.execute(update(StudyMessage).where(
            StudyMessage.id == message_id, StudyMessage.status == "generating",
            StudyMessage.expires_at <= func.now(),
        ).values(status="interrupted", error=expired_reply_error(), expires_at=None))
        await session.commit()
        return bool(result.rowcount)


async def message_stream(message_id):
    """Coalesce live snapshots and periodically reconcile with the durable reply."""
    channel = _STREAMS.setdefault(message_id, _StreamChannel())
    ready = asyncio.Event()
    channel.listeners.add(ready)
    previous = None
    last_heartbeat = time.monotonic()
    next_poll = last_heartbeat
    durable = None
    try:
        while True:
            ready.clear()
            now = time.monotonic()
            latest = channel.latest
            if durable is None or now >= next_poll or (latest and latest["attempt"] > durable["attempt"]):
                async with SessionLocal() as session:
                    row, expired = (await session.execute(select(StudyMessage, StudyMessage.expires_at <= func.now())
                        .where(StudyMessage.id == message_id))).one_or_none() or (None, False)
                if row is None:
                    return
                if row.status == "generating" and expired:
                    await expire_message_if_stale(message_id)
                    continue
                durable = stream_snapshot(row)
                latest = channel.latest
                next_poll = time.monotonic() + STREAM_POLL_SECONDS

            snapshot = durable
            if latest and latest["attempt"] == durable["attempt"] and durable["status"] == "generating":
                snapshot = latest
            if snapshot != previous:
                yield "data: " + json.dumps(snapshot, ensure_ascii=False) + "\n\n"
                previous = snapshot
            if snapshot["status"] != "generating":
                return
            now = time.monotonic()
            if now - last_heartbeat >= STREAM_HEARTBEAT_SECONDS:
                yield ": keep-alive\n\n"
                last_heartbeat = now
            if not ready.is_set():
                try:
                    await asyncio.wait_for(ready.wait(), timeout=max(0, next_poll - time.monotonic()))
                except TimeoutError:
                    pass
    finally:
        channel.listeners.discard(ready)
        _release_channel(message_id, channel)
