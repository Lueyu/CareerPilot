"""Deterministic tutoring policy. Credentials come from server configuration only."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.config import Settings, get_settings

POLICY_VERSION = "study-auto-v2.2"
STREAM_CAPABILITIES = frozenset({"text", "stream"})
ROLE_BY_ACTION = {
    "interview": "interviewer", "critique": "answer_reviewer", "jd": "jd_coach",
    "project": "project_coach", "improve": "project_coach",
    "diagram": "artifact_builder", "demo": "artifact_builder",
}
PROJECT_REFERENCE = re.compile(r"项目|仓库|源码|代码|文件|调用链|\brepo(?:sitory)?\b", re.I)
DIRECT_DEFINITION = re.compile(
    r"(?:请问|请)?(?:什么是|啥是|什么叫|(?:what is (?:a|an)|define)\s+)"
    r"[^，。；？!?;\n]{1,45}[？?。]?", re.I,
)
APPLIED_REASONING = (
    ("multiple_constraints", "需要同时考虑多项约束", re.compile(
        r"既要.{0,100}(?:又要|还要)|同时(?:满足|保证|兼顾|控制|避免)|"
        r"兼顾|在.{1,48}(?:约束|前提|预算|限制|条件)下|"
        r"平衡.{1,40}(?:与|和|及)|under .{1,48} constraints?|"
        r"must .{1,60} while .{1,60}", re.I,
    )),
    ("compare_alternatives", "需要比较方案及其适用条件", re.compile(
        r"取舍|权衡|选型|备选方案|各自.{0,12}(?:优缺点|适合|适用|风险)|"
        r"(?:相比|对比|比较|区别|二选一).{0,60}(?:选|场景|影响|风险|优缺点|适用|推荐)|"
        r"trade.?offs?|pros and cons|compare .{1,60}(?:choose|recommend|trade.?off)", re.I,
    )),
    ("diagnose_failure", "需要定位原因、验证假设或分析失败条件", re.compile(
        r"排查|根因|故障复盘|从(?:哪|何处).{0,10}查|定位.{0,20}(?:原因|问题|故障)|"
        r"失败分支|边界条件|故障窗口|如何验证.{0,12}(?:原因|假设)|"
        r"(?:崩溃|宕机|失败|重试|恢复|回滚).{0,30}(?:之前|之后|重复|丢失|如何|会怎样)|"
        r"troubleshoot|diagnos(?:e|is)|failure mode|root cause", re.I,
    )),
    ("design_solution", "需要设计可执行的方案或验证步骤", re.compile(
        r"(?:设计|规划|拟定|搭建|制定).{0,40}"
        r"(?:方案|流程|架构|机制|接口|策略|用例|监控|测试|上线|迁移|回滚|系统|服务|验证|模型)|"
        r"(?:怎么|如何|请|帮我)(?:设计|规划|搭建|制定)|"
        r"design (?:an? |the )?(?:system|service|pipeline|workflow|schema|plan|strategy)", re.I,
    )),
)
CONTINUATION = (
    "这一步", "刚才", "上面", "为什么会", "接着", "那里", "它为什么", "继续", "进一步",
    "这个方案", "为什么不", "那如果", "上一轮", "这一层",
)

# A situation followed by a request for a decision is common outside technical roles.
SCENARIO_CLAUSE = re.compile(r"[。！？；，;!?\n]|(?<!\d)[.,](?=\s)|\b(?:but|while|given that)\b", re.I)
SCENARIO_DECISION = re.compile(
    r"怎么(?:办|做|定|选|查|判断|调整|处理|验证)|如何|怎样|怎么办|该(?:先|选|做|如何|怎么)|"
    r"要不要|能否(?:判断|保证)|优先(?:考虑|处理|解决)|请(?:提出|评估|推荐|给出)|"
    r"\b(?:what (?:would|should|could)|which (?:would|should)|how (?:would|should|can|do) (?:we|you)|"
    r"recommend|prioriti[sz]e|evaluate|investigate|decide|propose|assess)\b", re.I,
)
COUNTERFACTUAL = re.compile(r"如果|假如|假设|一旦|即使|倘若|\b(?:suppose|imagine|what if|even if|unless|if)\b", re.I)
BOUNDED_ANSWER = re.compile(
    r"一句话|用.{0,4}句话|举.{0,4}例子|是什么|什么意思|什么是|名词解释|"
    r"\b(?:in one sentence|give (?:me )?an example|what (?:is|does .+ mean)|define)\b", re.I,
)


def needs_scenario_reasoning(question):
    clauses = [part.strip() for part in SCENARIO_CLAUSE.split(question) if part.strip()]
    if len(clauses) < 2 or not SCENARIO_DECISION.search(question):
        return False
    if COUNTERFACTUAL.search(question):
        return True
    return not BOUNDED_ANSWER.search(question) and any(
        len(clause) >= 8 and not SCENARIO_DECISION.search(clause) for clause in clauses
    )


@dataclass(frozen=True)
class TutoringIntent:
    role: str
    preferred_tier: str
    reasons: tuple[tuple[str, str], ...]
    specialties: frozenset[str]
    evidence: str
    code_files: int


def tutoring_intent(action, question, prior, sources, artifact, proactive_diagram, project_id=None):
    prior_trace = ((prior.coach or {}).get("trace") or {}) if prior else {}
    continues = bool(prior and prior.status == "completed" and prior.project_id == project_id
                     and any(cue in question for cue in CONTINUATION))
    code_paths = {
        s["path"] for s in sources
        if s.get("provenance") == "repository_observation" and s.get("path")
        and s.get("text") and not s["path"].lower().endswith((".md", ".rst"))
    }
    project_turn = action in {"project", "improve"} or (
        action not in {"interview", "critique", "jd", "simplify", "example"}
        and bool(project_id and (PROJECT_REFERENCE.search(question) or (
            continues and (prior_trace.get("role") == "project_coach" or prior.action in {"project", "improve"})
        )))
    )
    version_gap = any(
        s.get("kind") == "scope_gap"
        and str(s.get("id", "")).endswith((":stale-analysis", ":revision-mismatch"))
        for s in sources
    )
    report = any(s.get("provenance") == "project_report" for s in sources)
    jd = any(s.get("provenance") == "imported_jd" and s.get("text") for s in sources)
    role = "project_coach" if project_turn and not artifact else ROLE_BY_ACTION.get(action, "tutor")
    reasons = []
    specialties = set()
    evidence = "not_required"
    if project_turn:
        evidence = "code_with_version_gap" if code_paths and version_gap else (
            "code_available" if code_paths else "stale" if version_gap else "report_only" if report else "missing"
        )
        if code_paths:
            specialties.add("code_reasoning")
            reasons.append(("project_synthesis", "需要根据本轮代码材料分析项目"))
            if len(code_paths) > 1:
                reasons.append(("cross_file_evidence", "需要综合多个文件的代码证据"))
        else:
            reasons.append(("project_evidence_gap", "代码证据不足，先说明材料缺口与可核实范围"))
        if version_gap:
            reasons.append(("version_gap", "历史分析与当前版本需区分"))
    elif action == "jd":
        evidence = "jd_available" if jd else "missing"
        reasons.append(("jd_synthesis", "需要结合岗位原文给出练习重点") if jd else
                       ("jd_evidence_gap", "缺少岗位原文，需先取得岗位描述"))

    if artifact or proactive_diagram:
        specialties.add("structured_text")
        reasons.append(("structured_artifact", "需要生成可校验的教学产物") if artifact else
                       ("proactive_diagram", "图解有助于展示步骤、关系或故障分支"))

    followup = (
        continues and (prior_trace.get("preferred_tier") or prior_trace.get("tier")) == "strong"
        and action not in {"simplify", "example", "interview", "jd"}
    )
    can_analyze = (not project_turn or bool(code_paths)) and (action != "jd" or jd)
    depth_reasons = []
    if can_analyze and action not in {"simplify", "example"} and not DIRECT_DEFINITION.fullmatch(question.strip()):
        depth_reasons = [(code, reason) for code, reason, pattern in APPLIED_REASONING if pattern.search(question)]
        if not depth_reasons and needs_scenario_reasoning(question):
            depth_reasons.append(("scenario_decision", "需要结合题设判断下一步，并说明判断依据"))
    reasons.extend(depth_reasons)
    if followup and can_analyze:
        reasons.append(("grounded_followup", "承接本分支上一轮的深入讨论"))

    strong = bool(specialties or depth_reasons or (jd and action == "jd") or (can_analyze and followup))
    if not reasons:
        reasons.append(("direct_teaching", "可直接讲解、澄清或进行一轮面试练习"))
    return TutoringIntent(role, "strong" if strong else "flash", tuple(reasons),
                          frozenset(specialties), evidence, len(code_paths))


def profile(base, tier, settings=None):
    if settings is None:
        settings = get_settings()
    inherited = {k: v for k, v in base.items() if k != "thinking"}
    if tier == "flash":
        flash = {**inherited, "model": settings.study_flash_model or base.get("model")}
        thinking = getattr(settings, "study_flash_thinking", None)
        if thinking in ("disabled", "enabled"):
            flash["thinking"] = thinking
        return flash
    if not settings.study_strong_model:
        return None
    # A different provider must supply its own endpoint AND credential.
    if any((settings.study_strong_base_url, settings.study_strong_api_key, settings.study_strong_provider)):
        if not (settings.study_strong_base_url and settings.study_strong_api_key):
            return None
        return {"model": settings.study_strong_model, "provider": settings.study_strong_provider or "compatible",
                "base_url": settings.study_strong_base_url, "api_key": settings.study_strong_api_key}
    return {**inherited, "model": settings.study_strong_model}


@dataclass(frozen=True)
class TutoringProfile:
    tier: str
    config: dict = field(repr=False)
    capabilities: frozenset[str]
    context: int
    output_tokens: int
    identity: tuple = field(repr=False)


def _endpoint_identity(config):
    # The adapter targets the same path even when the two profiles use
    # different provider labels or keys.
    try:
        url = urlsplit(config["base_url"].rstrip("/"))
        if url.scheme not in {"http", "https"} or not url.hostname:
            return None
        return url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80), url.path, config["model"]
    except ValueError:
        return None


def tutoring_profiles(base: dict, settings: Settings, mode: str, intent: TutoringIntent):
    profiles = []
    skipped = {}
    for tier in ("flash", "strong") if mode == "auto" else (mode,):
        config = profile(base, tier, settings)
        if not config or not all(isinstance(config.get(k), str) and config[k].strip()
                                 for k in ("model", "api_key", "base_url", "provider")):
            skipped[tier] = "unavailable"
            continue
        identity = _endpoint_identity(config)
        if identity is None:
            skipped[tier] = "unavailable"
            continue
        capabilities = frozenset(getattr(settings, f"study_{tier}_capabilities"))
        if not STREAM_CAPABILITIES <= capabilities:
            skipped[tier] = "capability"
            continue
        profiles.append(TutoringProfile(
            tier, config, capabilities, getattr(settings, f"study_{tier}_context"),
            getattr(settings, f"study_{tier}_output_tokens"), identity,
        ))
    profiles.sort(key=lambda candidate: (
        len(intent.specialties - candidate.capabilities), candidate.tier != intent.preferred_tier,
    ))
    return profiles, skipped


def transient_failure(error, stream_error):
    if error.get("kind") == "http":
        status = error.get("status")
        return type(status) is int and (status in {408, 429} or 500 <= status <= 599)
    return error.get("kind") in {"network", "timeout"} or stream_error in {
        "empty_content", "missing_finish", "output_truncated", "provider_error",
    }
