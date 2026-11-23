"""Public coach prompts must fit the real gateway without provider calls."""

import json

import pytest

from app.models.public_coach import CoachCandidate, CoachThread, CoachTurn
from app.services.public_coach import OUTPUT_BUDGET, TurnContext
from app.services.public_model import _prepare


@pytest.fixture(params=["中文", '用户🚀é"\\\n'], ids=["chinese", "multilingual"])
def long_memories(request):
    return [(f"经历{number}：" + request.param * 1200)[:2400] for number in range(16)]


def accepted_prompt(context):
    messages = context.messages()
    _, clean, _ = _prepare("coach:1:1", messages, OUTPUT_BUDGET[context.turn.action])
    return json.loads(clean[1]["content"])


@pytest.mark.parametrize("action", ["chat", "generate"])
def test_long_study_prompt_keeps_current_request_and_edit_target(action, long_memories):
    instruction = "请修改或解释："
    current_text = instruction + "\x01" * (6000 - len(instruction))
    thread = CoachThread(mode="study", role="产品运营", direction="用户增长", question_title="为何？")
    history = [
        CoachTurn(id=index, action="chat", status="completed", user_text="旧问题" * 2000, reply="旧回复" * 1000)
        for index in range(1, 33)
    ]
    candidates = [
        CoachCandidate(number=number, title=f"{number}：" + "候选题🚀" * 98, superseded=False)
        for number in range(1, 401)
    ]
    target = candidates[-1]
    candidates.append(CoachCandidate(number=target.number, title="已替换的同号题", superseded=True))
    turn = CoachTurn(action=action, user_text=current_text, target_number=target.number if action == "generate" else None)

    prompt = accepted_prompt(TurnContext(thread, turn, history, candidates, long_memories))

    assert prompt["当前用户原文"] == current_text
    assert prompt["题目"] == thread.question_title
    assert prompt["近几轮记录"]
    assert prompt["用户确认的经历"][:1] == long_memories[:1]
    if action == "generate":
        assert prompt["明确指定的修改题号"] == target.number
        selected = prompt["当前候选题"]
        assert 1 < len(selected) < len(candidates) - 1
        assert [item["题号"] for item in selected] == sorted({item["题号"] for item in selected})
        assert selected[-1] == {"题号": target.number, "题干": target.title}


def test_candidate_numbers_are_bounded_even_when_titles_are_short():
    thread = CoachThread(mode="study", role="产品运营", direction="用户增长")
    turn = CoachTurn(action="generate", user_text="生成新题")
    candidates = [
        CoachCandidate(number=number, title="题", superseded=False) for number in range(1, 5001)
    ]

    prompt = accepted_prompt(TurnContext(thread, turn, [], candidates, []))

    assert 0 < len(prompt["当前候选题"]) < len(candidates)
    assert prompt["当前候选题"][0] == {"题号": 1, "题干": "题"}


def test_interview_feedback_keeps_both_answers_and_followup(long_memories):
    thread = CoachThread(mode="interview", role="产品运营", direction="用户增长", question_title="为何？")
    initial_text = "原答" + "\x01" * 5998
    followup_text = "追答" + "\x02" * 5998
    followup = "怎么验证？"
    first_answer = CoachTurn(
        id=2, action="answer", user_text=initial_text, assisted=True, status="completed",
        reply=followup, result={"quote": "原答", "question": followup},
    )
    last_answer = CoachTurn(
        id=4, action="followup_answer", user_text=followup_text, assisted=True, status="completed", reply="",
    )
    history = [
        CoachTurn(id=1, action="hint", reply="\x03" * 1600, status="completed"),
        first_answer,
        CoachTurn(id=3, action="hint", reply="\x04" * 1600, status="completed"),
        last_answer,
    ]

    prompt = accepted_prompt(TurnContext(thread, CoachTurn(action="finish"), history, [], long_memories))

    assert prompt["题目"] == thread.question_title
    answers = {item["动作"]: item for item in prompt["近几轮记录"]}
    assert answers["answer"]["用户原文"] == initial_text
    assert answers["answer"]["结构化结果"]["question"] == followup
    assert answers["answer"]["已获提示"] is True
    assert answers["followup_answer"]["用户原文"] == followup_text
