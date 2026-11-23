"""Deterministic tutoring policy. Credentials come from server configuration only."""
from app.config import get_settings

DEEP_ACTIONS = {"project", "improve", "diagram", "demo", "mechanism", "critique", "jd"}
SYSTEM_TERMS = ("checkpoint", "检查点", "幂等", "副作用", "一致性", "恢复", "事务", "重试", "架构", "故障")
FOLLOWUPS = ("这一步", "刚才", "上面", "为什么会", "接着", "那里", "它为什么", "继续", "进一步")


def decide(mode, action, question, history, sources):
    reasons = []
    if action in DEEP_ACTIONS:
        reasons.append("请求深入讲解、项目依据或结构化产物")
    if sum(term in question.lower() for term in SYSTEM_TERMS) >= 2:
        reasons.append("问题涉及多个相互影响的工程机制")
    if len({s.get("path") for s in sources if s.get("path")}) >= 2:
        reasons.append("需要综合多个文件的证据")
    if any("stale" in str(s.get("id", "")) or "mismatch" in str(s.get("id", "")) for s in sources):
        reasons.append("历史报告与当前版本需交叉核实")
    previous = next((m for m in history if m.role == "assistant"), None)
    if previous and any(t in question for t in FOLLOWUPS) and (
        (previous.coach or {}).get("trace", {}).get("tier") == "strong" or previous.action in DEEP_ACTIONS
    ):
        reasons.append("承接上轮深度分析")
    if mode != "auto":
        return mode, "用户手动选择", bool(reasons)
    return ("strong", "；".join(reasons), True) if reasons else ("flash", "概念、简要澄清或已有材料的整理", False)


def profile(base, tier):
    settings = get_settings()
    if tier == "flash":
        return {**base, "model": settings.study_flash_model or base.get("model")}
    if not settings.study_strong_model:
        return None
    # A different provider must supply its own endpoint AND credential.
    if any((settings.study_strong_base_url, settings.study_strong_api_key, settings.study_strong_provider)):
        if not (settings.study_strong_base_url and settings.study_strong_api_key):
            return None
        return {"model": settings.study_strong_model, "provider": settings.study_strong_provider or "compatible",
                "base_url": settings.study_strong_base_url, "api_key": settings.study_strong_api_key}
    return {**base, "model": settings.study_strong_model}
