"""Mock interview evidence is saved chat, scoped to one run and step boundary."""
import re
from uuid import uuid4

from app.i18n import request_language


def reject(zh: str, en: str):
    raise ValueError(en if request_language() == "en" else zh)


def initialize(run: dict, boundary: int) -> dict:
    if run.get("run_id"):
        return run
    return {**run, "run_id": uuid4().hex, "step_revision": run["revision"],
            "boundary_message_id": boundary}


def binding(run: dict, purpose: str) -> dict:
    return {"run_id": run["run_id"], "step": run["current"],
            "revision": run["revision"], "step_revision": run["step_revision"],
            "boundary_message_id": run.get("boundary_message_id", 0), "purpose": purpose}


def progress(run: dict, messages) -> dict:
    scope = binding(run, "mock_question")
    turns = [m for m in messages if m.id > run.get("boundary_message_id", 0)
             and all((m.coach or {}).get("workflow", {}).get(k) == scope[k]
                     for k in ("run_id", "step", "step_revision"))]
    questions = [m for m in turns if m.role == "assistant"
                 and m.coach["workflow"]["purpose"] == "mock_question"]
    reports = [m for m in turns if m.role == "assistant"
               and m.coach["workflow"]["purpose"] == "mock_report"]
    answers = [m for m in turns if m.role == "user"
               and m.coach["workflow"]["purpose"] == "mock_answer"]
    question = questions[-1] if questions else None
    answer = answers[-1] if answers else None
    feedback = next((m for m in reversed(turns) if m.role == "assistant" and answer
                     and m.request_id == answer.request_id and m.id > answer.id), None)
    report = reports[-1] if reports else None
    reply = report or feedback or question
    if reply and reply.status in ("failed", "interrupted"):
        phase = "failed"
    elif report:
        phase = "ready" if report.status == "completed" and report.content.strip() else "report"
    elif feedback:
        phase = "ready" if feedback.status == "completed" and feedback.content.strip() else "feedback"
    elif question:
        phase = "answer" if question.status == "completed" and question.content.strip() else "question"
    else:
        phase = "prepare"
    return {"phase": phase, "question_message_id": question.id if question else None,
            "answer_message_id": answer.id if answer else None,
            "feedback_message_id": feedback.id if feedback else None, "report_message_id": report.id if report else None,
            "reply_message_id": reply.id if reply else None}


COMMAND = re.compile(
    r"(?:/?(?:下一步|下一题|下一个|完成本步|完成|开始面试(?:准备)?|开始模拟面试|这步完成|"
    r"(?:暂停|先暂停)(?:技能|流程|训练|模拟面试|面试|一下)?|"
    r"(?:继续|恢复)(?:技能|流程|训练|模拟面试|面试)?|"
    r"跳过(?:这|本|当前)?(?:一步|一题|步骤|题|环节|阶段)?(?:（会记录）)?|"
    r"进入(?:准备|自我介绍|技术问答|系统设计|行为问题|复盘)|结束面试[，,]\s*查看复盘|"
    r"请面试官提问|提交回答|生成复盘报告|重试本环节回复|查看最终复盘报告|查看本环节点评|"
    r"next(?:\s+(?:step|stage|question))?|(?:continue|pause|resume)(?:\s+interview)?|"
    r"skip(?:\s+(?:this\s+|current\s+)?(?:step|stage|question))?(?:\s+\(recorded\))?|"
    r"enter\s+(?:preparation|introduction|technical questions|system design|behavioral questions|review)|"
    r"start interview preparation|ask the stage question|submit answer|generate interview report|retry stage reply|"
    r"view final interview report|view stage feedback|"
    r"finish interview and view review|advance|start|done|"
    r"(?:把|将).{1,60}?(?:换成|改为|改成).{1,120})[。！？!?.\s]*|/\S+)", re.IGNORECASE,
)


def validate_turn(run: dict | None, body, messages) -> dict | None:
    fields = (body.workflow_run_id, body.workflow_step, body.workflow_revision)
    if body.purpose is None:
        if any(value is not None for value in fields):
            reject("请完整指定本轮作答的用途和步骤。", "Specify the purpose and step for this turn.")
        return None
    if not run or run["id"] != "mock" or run["status"] != "running":
        reject("请先开始或恢复模拟面试。", "Start or resume the mock interview first.")
    if not run.get("run_id"):
        reject("请重新读取面试进度后继续。", "Reload the interview before continuing.")
    if fields != (run["run_id"], run["current"], run["revision"]):
        reject("面试步骤已更新，请刷新后重试。", "The interview step changed. Refresh and try again.")
    state = progress(run, messages)
    if body.purpose in ("mock_question", "mock_report"):
        if (body.purpose == "mock_report") != (run["current"] == len(run["steps"]) - 1):
            reject("请使用当前环节的提问或复盘操作。", "Use the question or report action for the current stage.")
        if state["phase"] != "prepare":
            reject("本环节已经提问，请回答或重试原回复。", "This stage already has a question. Answer it or retry its reply.")
    else:
        if state["phase"] != "answer":
            reject("请先等待本环节提问完成，再提交回答。", "Wait for the stage question before answering.")
        if COMMAND.fullmatch(body.content.strip()) or body.content.startswith(("继续「", "先给我完整模拟面试", "请开始当前环节")):
            reject("请输入你自己的回答；操作指令不能作为作答。", "Write your own answer. A navigation command is not an answer.")
    if body.agent_role != "interviewer" or body.revision_of is not None:
        reject("面试作答须由当前面试官处理。", "Interview turns must use the current interviewer.")
    return binding(run, body.purpose)


def validate_advance(run: dict, messages):
    state = progress(run, messages)
    if state["phase"] != "ready":
        if run["current"] == len(run["steps"]) - 1:
            reject("请等待最终复盘报告完整保存后再结束面试。", "Wait for the final report to be fully saved before finishing.")
        reject("请先提交本环节回答，并等待点评完成后再进入下一环节。",
               "Submit your answer and wait for completed feedback before advancing.")
    return state


def validate_retry(run: dict | None, reply):
    scope = (reply.coach or {}).get("workflow")
    if scope and (not run or run["id"] != "mock" or run["status"] != "running"
                  or any(scope.get(k) != binding(run, scope["purpose"])[k]
                         for k in ("run_id", "step", "step_revision"))):
        reject("该回复不属于当前进行中的面试环节。", "This reply is not part of the current running interview stage.")
