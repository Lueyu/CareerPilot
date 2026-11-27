from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app import project_worker as worker
from app.services import assessment_jobs, assessment_worker, project_scheduler, study_notes


@pytest.mark.asyncio
async def test_continuous_grading_and_notes_leave_a_turn_for_project_work(monkeypatch, db_session):
    session = AsyncMock()
    session.get.return_value = None
    session.execute.side_effect = db_session.execute

    @asynccontextmanager
    async def sessions():
        yield session

    serviced = []

    async def grade(*args):
        serviced.append("grade")
        if len(serviced) > 3:
            pytest.fail("worker kept servicing an earlier queue")

    async def note(*args):
        serviced.append("note")
        return True

    class EndOfRound(Exception):
        pass

    async def project(*args):
        serviced.append("project")
        raise EndOfRound

    monkeypatch.setattr(worker, "worker_heartbeat", AsyncMock())
    monkeypatch.setattr(project_scheduler, "tick", AsyncMock())
    monkeypatch.setattr(worker, "reconcile", AsyncMock())
    monkeypatch.setattr(assessment_worker, "reconcile", AsyncMock())
    monkeypatch.setattr(assessment_jobs, "claim_step", AsyncMock(return_value=object()))
    monkeypatch.setattr(worker.jobs, "claim_step", AsyncMock(return_value=object()))
    monkeypatch.setattr(assessment_worker, "execute_claim", grade)
    monkeypatch.setattr(study_notes, "process_next_note_job", note)
    monkeypatch.setattr(worker, "execute_claim", project)
    with pytest.raises(EndOfRound):
        await worker.serve(factory=sessions)
    assert serviced == ["grade", "note", "project"]
