from uuid import uuid4

from fastapi import HTTPException
import pytest

from app.models.study_chat import StudyMessage, StudySession
from app.services import study_notes, study_practice


@pytest.mark.asyncio
async def test_selected_reply_is_the_practice_and_note_focus_and_cannot_cross_sessions(db_session, test_workspace):
    db, ws = db_session, test_workspace
    chat, other = [StudySession(workspace_id=ws.id, title=name, context={'topic': '学习会话'}) for name in ('原会话', '其他会话')]
    db.add_all([chat, other]); await db.flush()
    def turn(sid, question, answer):
        key = uuid4().hex
        return [StudyMessage(session_id=sid, role=role, status='completed', action='explain',request_id=key,content=text) for role,text in [('user',question),('assistant',answer)]]
    first, second, foreign = turn(chat.id,'幂等键如何生效？','以唯一键去重'),turn(chat.id,'readiness 做什么？','决定接流量'),turn(other.id,'其他问题','另一个会话')
    db.add_all([*first,*second,*foreign]);await db.commit()
    q,answer,point,origin = await study_practice._material(db,chat,None,first[1].id)
    assert (q,answer,point,origin['focus_message_id']) == (first[0].content,first[1].content,first[0].content,first[1].id)
    job = await study_notes.enqueue(db,ws.id,chat.id,'focus-a',None,first[1].id)
    assert [m['id'] for m in job.snapshot if m.get('study_focus')] == [first[1].id]
    assert second[1].id in {m['id'] for m in job.snapshot}  # later corrections remain available
    with pytest.raises(HTTPException) as mismatch:
        await study_notes.enqueue(db,ws.id,chat.id,'focus-a',None,second[1].id)
    assert mismatch.value.status_code == 409
    with pytest.raises(HTTPException) as wrong:
        await study_practice._material(db,chat,None,foreign[1].id)
    assert wrong.value.status_code == 404
    with pytest.raises(HTTPException) as denied:
        await study_notes.enqueue(db,ws.id,chat.id,'focus-b',None,foreign[1].id)
    assert denied.value.status_code == 404
