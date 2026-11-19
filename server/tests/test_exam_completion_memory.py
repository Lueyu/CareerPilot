from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.api.v1.memory import confirm_memory, delete_memory, list_memory
from app.models import Exam, ExamAnswer, ExamQuestion, Question, Workspace


@pytest.mark.asyncio
async def test_completed_answered_exams_backfill_and_sync_once(db_session, test_workspace):
    other = Workspace(slug=f"exam-memory-{uuid4().hex}", name="Other workspace")
    db_session.add(other)
    await db_session.flush()

    def exam(workspace, status="COMPLETED", **answer):
        question = Question(short_code=f"exam-{uuid4().hex[:16]}", workspace_id=workspace.id,
                            title="Example question", topic="memory", qtype="open")
        return Exam(workspace_id=workspace.id, title="Original paper", kind="diagnosis",
                    status=status, completed_at=datetime.now(timezone.utc), score=None,
                    questions=[ExamQuestion(question=question, order_no=1, answer=ExamAnswer(**answer))])

    historic = exam(test_workspace, choice_index=0)
    foreign = exam(other, text_answer="Original answer")
    ignored = [exam(test_workspace, text_answer=" \t\n　"), exam(test_workspace),
               exam(test_workspace, "ABORTED", text_answer="answer"),
               exam(test_workspace, "CREATED", text_answer="answer"),
               exam(test_workspace, "SCORING", text_answer="answer"),
               exam(test_workspace, "SCORING_FAILED", text_answer="answer")]
    db_session.add_all([historic, foreign, *ignored])
    await db_session.commit()
    # The migration's unscoped call backfills all workspaces.
    await db_session.execute(text("SELECT capture_exam_completion_memory()"))
    await db_session.commit()
    candidate, = (await list_memory(ws=test_workspace, session=db_session))["items"]
    assert candidate["meta"]["exam_id"] == historic.id
    assert candidate["source_ref"] == f"exam_completion:{historic.id}"
    assert candidate["status"] == "CANDIDATE"
    assert "score" not in candidate["meta"]
    assert (await list_memory(ws=test_workspace, session=db_session))["items"] == [candidate]
    await confirm_memory(candidate["id"], ws=test_workspace, session=db_session)
    confirmed, = (await list_memory(ws=test_workspace, session=db_session))["items"]
    assert confirmed["status"] == "ACTIVE" and not confirmed["eligible_for_personalization"]
    await delete_memory(confirmed["id"], ws=test_workspace, session=db_session)

    fresh = exam(test_workspace, transcript="Spoken answer")
    fresh_foreign = exam(other, text_answer="Private answer")
    db_session.add_all([fresh, fresh_foreign])
    await db_session.commit()
    fresh_candidate, = (await list_memory(ws=test_workspace, session=db_session))["items"]
    assert fresh_candidate["meta"]["exam_id"] == fresh.id
    assert await db_session.scalar(text("SELECT count(*) FROM memory_items WHERE source_ref = :key"),
                                   {"key": f"exam_completion:{fresh_foreign.id}"}) == 0
    other_items = (await list_memory(ws=other, session=db_session))["items"]
    assert {item["meta"]["exam_id"] for item in other_items} == {foreign.id, fresh_foreign.id}
