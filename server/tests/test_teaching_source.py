"""Teaching uses the assessed frozen source, not current same-domain questions."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import Exam, ExamQuestion, LearningTask, Question, Skill
from app.services import teaching


class CapturingAdapter:
    active = True

    def __init__(self):
        self.prompts = []

    async def chat(self, system, user, **kwargs):
        self.prompts.append(user)
        return {"content": json.dumps({"analogy": "a", "example": "b", "principles": ["c"],
                                       "steps": ["d"], "verify": "e", "text": "source explanation"})}


async def source_task(session, workspace):
    suffix = uuid4().hex[:12]
    skill = Skill(key=f"teaching-{suffix}", name="Source skill")
    q = Question(short_code=f"source-{suffix}", workspace_id=workspace.id, skill=skill,
                 title="MUTABLE_LIVE_TITLE", qtype="open", topic=skill.key,
                 answer_html="MUTABLE_LIVE_ANSWER", rubric={"points": [{"title": "MUTABLE_RUBRIC"}]})
    eq = ExamQuestion(question=q, order_no=1, frozen_snapshot={
        "title": "FROZEN_SOURCE_TITLE", "answer_html": "<p>FROZEN_SOURCE_ANSWER</p>",
        "rubric": {"points": [{"title": "FROZEN_CRITERION", "detail": "FROZEN_STANDARD"}]},
    })
    exam = Exam(workspace_id=workspace.id, title="Source exam", kind="diagnosis",
                status="COMPLETED", questions=[eq])
    session.add(exam)
    await session.flush()
    session.add(Question(short_code=f"unrelated-{suffix}", workspace_id=workspace.id,
                         skill_id=skill.id, title="UNRELATED_SAME_DOMAIN", qtype="open",
                         topic=skill.key, difficulty=1, answer_html="UNRELATED_GRAPH_MEMORY_SPATIAL"))
    task = LearningTask(workspace_id=workspace.id, skill_id=skill.id, title="补强原评分项",
                        kind="UNDERSTAND", source_exam_id=exam.id, source_exam_question_id=eq.id,
                        source_criterion="FROZEN_CRITERION", acceptance="学习后新题验证",
                        content={"observation": {"answer_excerpt": "ORIGINAL_USER_ANSWER",
                                 "criterion": "FROZEN_CRITERION", "criterion_detail": "FROZEN_STANDARD"}})
    session.add(task)
    await session.flush()
    return task, skill


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["build", "help"])
@pytest.mark.parametrize("old_cache", [False, True])
async def test_teaching_and_help_use_only_frozen_source(db_session, test_workspace, operation, old_cache):
    task, skill = await source_task(db_session, test_workspace)
    if old_cache:
        task.content = {**task.content, "analogy": "OLD_DOMAIN_TEACHING", "source": "llm",
                        "principles": ["OLD_DOMAIN_PRINCIPLE"],
                        "help_cache": {"simpler": {"text": "OLD_DOMAIN_HELP", "source": "llm"}}}
    adapter = CapturingAdapter()
    if operation == "build":
        await teaching.build_teaching_content(db_session, adapter, task=task,
                                             skill_name=skill.name, skill_key=skill.key)
    else:
        await teaching.help_answer(db_session, adapter, task=task, skill_name=skill.name,
                                   skill_key=skill.key, mode="simpler")
    assert adapter.prompts, "Legacy source-task caches must regenerate once"
    prompt = adapter.prompts[0]
    assert all(value in prompt for value in ("FROZEN_SOURCE_TITLE", "FROZEN_SOURCE_ANSWER",
                                             "FROZEN_CRITERION", "FROZEN_STANDARD", "ORIGINAL_USER_ANSWER"))
    assert not any(value in prompt for value in ("MUTABLE_LIVE", "MUTABLE_RUBRIC", "UNRELATED_", "OLD_DOMAIN"))
    assert task.content["source_context_version"] == 2
    if operation == "build":
        await teaching.build_teaching_content(db_session, adapter, task=task,
                                             skill_name=skill.name, skill_key=skill.key)
    else:
        await teaching.help_answer(db_session, adapter, task=task, skill_name=skill.name,
                                   skill_key=skill.key, mode="simpler")
    assert len(adapter.prompts) == 1, "The current source context remains cached"


@pytest.mark.asyncio
async def test_source_material_must_belong_to_task_workspace(db_session, test_workspace):
    task, skill = await source_task(db_session, test_workspace)
    foreign_task = SimpleNamespace(**{name: getattr(task, name) for name in
        ("source_exam_question_id", "skill_id", "title", "kind", "acceptance", "why")},
        workspace_id=-1, content={})
    adapter = CapturingAdapter()
    await teaching.help_answer(db_session, adapter, task=foreign_task, skill_name=skill.name,
                               skill_key=skill.key, mode="simpler")
    assert "FROZEN_SOURCE_ANSWER" not in adapter.prompts[0]
    assert "MUTABLE_LIVE" not in adapter.prompts[0]
    assert "UNRELATED_" not in adapter.prompts[0]


@pytest.mark.asyncio
async def test_rule_teaching_keeps_source_context_and_does_not_claim_model_generation(db_session, test_workspace):
    task, skill = await source_task(db_session, test_workspace)
    content = await teaching.build_teaching_content(db_session, SimpleNamespace(active=False),
        task=task, skill_name=skill.name, skill_key=skill.key)
    assert content["source"] == "rule"
    assert content["source_context_version"] == 2
    assert "ORIGINAL_USER_ANSWER" in content["example"]
    assert "FROZEN_STANDARD" in " ".join(content["principles"])
    assert "UNRELATED_" not in json.dumps(content)
