"""Workspace-owned conversational workflows stored in the existing study session context."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WorkflowId = Literal["mock", "prep", "digest", "project", "review", "boost"]
AgentRole = Literal["interviewer", "exam", "coach", "analyst", "resume", "collect"]

STEPS: dict[WorkflowId, tuple[str, ...]] = {
    "mock": ("面试准备", "自我介绍", "技术问答", "系统设计", "行为问题", "复盘报告"),
    "prep": ("解析 JD", "更新能力对标", "生成学习计划", "每日练习", "周复盘"),
    "digest": ("导入面经", "提取考点", "生成试卷", "练习", "错题入库"),
    "project": ("选择项目", "分析岗位关注点", "优化描述", "生成问题", "生成话术"),
    "review": ("提取今日复习题", "作答", "检查掌握度", "更新复习计划"),
    "boost": ("评估", "定位短板", "专项训练", "重评估"),
}

ROLE_INSTRUCTIONS: dict[AgentRole, str] = {
    "interviewer": "Behave as a technical interviewer. When conducting a mock interview, ask one question at a time. When evaluating, cite the answer and explain the gap before suggesting a follow-up.",
    "exam": "Behave as an examiner. Explain the purpose, coverage, difficulty and timing of a proposed exam. Never claim an exam was generated or started by your reply. Keep solutions hidden if the user is taking a test.",
    "coach": "Behave as a study coach. Explain why the suggested step helps, give a concrete example and state what independent practice would check.",
    "analyst": "Behave as an evidence analyst. Separate observations, targets and unknowns. Use actual scores or metrics only when they are in supplied sources; explain the reasoning and its limits.",
    "resume": "Behave as a resume adviser. Use STAR and distinguish user-confirmed experience from code or job description evidence. Never invent responsibilities, numbers, impact or personal experience.",
    "collect": "Behave as a materials curator. Explain what the supplied material supports and what review is needed. Never claim records were imported or saved by your reply.",
}


class WorkflowChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    operation: Literal["start", "pause", "resume", "skip", "advance", "change"]
    expected_revision: int = Field(ge=0)
    workflow: WorkflowId | None = None
    step: int | None = Field(default=None, ge=0)
    label: str | None = Field(default=None, min_length=1, max_length=120)


def change_workflow(current: dict | None, command: WorkflowChange, *, answer_ready: bool = False) -> dict:
    if command.expected_revision != (current or {}).get("revision", 0):
        raise ValueError("技能进度已在其他窗口更新，请刷新后重试。")
    revision = command.expected_revision + 1
    op = command.operation
    if op == "start":
        if command.workflow is None:
            raise ValueError("请选择要启动的技能。")
        result = dict(id=command.workflow, revision=revision, current=0, status="running",
                      steps=list(STEPS[command.workflow]), skipped=[])
        if command.workflow == "mock":
            from app.services.mock_interview import initialize
            result = initialize(result, 0)
            result["completed_steps"] = []
        return result
    if not current or current["status"] == "completed":
        raise ValueError("请先启动一项技能。")
    result = {**current, "revision": revision}
    if op in ("pause", "resume"):
        result["status"] = "paused" if op == "pause" else "running"
        return result
    if op == "change":
        step = current["current"] if command.step is None else command.step
        if step < current["current"] or step >= len(current["steps"]) or command.label is None:
            raise ValueError("请选择当前或后续步骤并填写新名称。")
        steps = list(current["steps"])
        steps[step] = command.label
        result["steps"] = steps
        return result
    if current["status"] == "paused":
        raise ValueError("请先继续技能，再推进步骤。")
    if current["id"] == "mock":
        from app.services.mock_interview import reject
        if op == "advance" and not answer_ready:
            reject("请先提交本环节回答并等待点评完成。", "Submit your answer and wait for completed feedback.")
        if op == "skip" and current["current"] == len(current["steps"]) - 1:
            reject("请等待复盘报告完成后再结束面试。", "Wait for the final report before finishing.")
        result["step_revision"] = revision
        if op == "advance":
            result["completed_steps"] = [*current.get("completed_steps", []), current["current"]]
    if op == "skip":
        result["skipped"] = [*current["skipped"], current["current"]]
    result["current"] = current["current"] + 1
    if result["current"] == len(current["steps"]):
        result["status"] = "completed"
    return result
