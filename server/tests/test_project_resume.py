import asyncio
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import Project, ProjectAnalysis, ProjectFinding, ProjectScan, Workspace


@pytest.mark.asyncio
async def test_resume_drafts_require_confirmation_and_isolate_revision_and_project(db_session, test_workspace):
    foreign_workspace = Workspace(slug=f'test-{uuid4().hex}', name='Other workspace', is_demo=False)
    db_session.add(foreign_workspace)
    await db_session.flush()
    foreign_id = foreign_workspace.id
    project, other_project, scan_project, foreign_project = [
        Project(workspace_id=workspace_id, name=name, owner='fixture', repo=name, repo_url='',
                meta={'profile': {'responsibilities': '已登记但未确认的职责', 'outcomes': '已登记但未确认的结果'}})
        for workspace_id, name in ((test_workspace.id, 'first'), (test_workspace.id, 'second'),
                                   (test_workspace.id, 'scanned'), (foreign_id, 'foreign'))
    ]
    db_session.add_all([project, other_project, scan_project, foreign_project])
    await db_session.flush()
    revision = 'a' * 40
    report = ProjectAnalysis(workspace_id=test_workspace.id, project_id=project.id, request_key='resume-fixture',
                             report_status='VALID', coverage_status='COMPLETE', source_revision=revision, report={
        'claims': [
            {'id': 'code', 'title': 'Repository only', 'text': 'A repository fact', 'kind': 'CODE_OBSERVED',
             'citations': [{'path': 'server/api.py', 'start_line': 5, 'end_line': 8}]},
            {'id': 'doc', 'title': 'Document only', 'text': 'A documentation claim', 'kind': 'DOC_CLAIM',
             'citations': [{'path': 'README.md', 'start_line': 5, 'end_line': 8}]},
        ],
        'issues': [{'id': 'I1', 'title': 'Suggestion only', 'recommendation': 'Improve metrics'}],
    })
    legacy = ProjectAnalysis(
        workspace_id=test_workspace.id, project_id=other_project.id, request_key='legacy-resume-fixture',
        status='SUCCESS', report={'architecture': [{'title': 'Legacy architecture', 'detail': 'Legacy assertion',
                                                     'citations': [{'path': 'README.md', 'start_line': 1, 'end_line': 4}]}]},
    )
    scan = ProjectScan(project_id=scan_project.id, workspace_id=test_workspace.id, status='SUCCESS')
    db_session.add_all([report, legacy, scan])
    await db_session.flush()
    db_session.add_all([
        ProjectFinding(project_id=scan_project.id, workspace_id=test_workspace.id, scan_id=scan.id,
                       level='VERIFIED', title=title, file_path=file_path)
        for title, file_path in [('Keyword match', 'module.py'), ('Language metadata', '(languages API)')]
    ])
    project.latest_valid_analysis_id = report.id
    project.last_seen_sha = revision
    await db_session.commit()

    path = f'/projects/{project.id}/resume'
    foreign_path = f'/projects/{foreign_project.id}/resume'
    headers = {'X-Workspace': test_workspace.slug, 'X-Api-Token': get_settings().api_token}
    payload = {'project_id': project.id, 'revision': 0, 'contribution': '  我实现权限校验。  ', 'confirmed': True}
    request_sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def request_session():
        async with request_sessions() as session:
            yield session

    app.dependency_overrides[get_session] = request_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://fixture/api/v1', headers=headers) as client:
            initial = await client.get(path)
            assert initial.status_code == 200, initial.text
            view = initial.json()
            assert view['draft'] is None
            assert view['profile'] == {'responsibilities': '已登记但未确认的职责', 'outcomes': '已登记但未确认的结果'}
            assert [item['title'] for item in view['evidence']] == ['Repository only']
            assert view['evidence'][0]['basis'] == 'code'
            assert view['evidence'][0]['source']['analysis_id'] == report.id
            assert view['evidence'][0]['citations'][0]['path'] == 'server/api.py'
            assert view['suggestions'][0]['detail'] == 'Improve metrics'

            assert (await client.get(foreign_path)).status_code == 404
            assert (await client.put(foreign_path, json={**payload, 'project_id': foreign_project.id})).status_code == 404
            assert (await client.put(path, json={**payload, 'project_id': other_project.id})).status_code == 409
            for bad_fields in ({'confirmed': False}, {'confirmed': 1}, {'contribution': '　 '}, {'content': 'Repo fact'}):
                assert (await client.put(path, json={**payload, **bad_fields})).status_code == 422

            saved = await client.put(path, json=payload)
            assert saved.status_code == 200, saved.text
            draft = saved.json()['draft']
            assert draft['revision'] == 1
            assert draft['content'] == f'{project.name}\n本人职责：我实现权限校验。'
            assert draft['contribution'] == '我实现权限校验。' and draft['outcome'] == ''
            assert (await client.get(path)).json()['draft'] == draft
            other = (await client.get(f'/projects/{other_project.id}/resume')).json()
            assert other['draft'] is None
            assert [item['title'] for item in other['evidence']] == ['Legacy architecture']
            assert other['evidence'][0]['basis'] == 'unclassified'
            assert other['evidence'][0]['source']['analysis_id'] == legacy.id
            assert other['evidence'][0]['citations'][0]['path'] == 'README.md'
            scanned = (await client.get(f'/projects/{scan_project.id}/resume')).json()['evidence']
            assert scanned == []
            assert (await client.put(path, json={**payload, 'contribution': 'stale'})).status_code == 409

            updates = ['改进输入校验', '增加回归测试']
            attempts = await asyncio.gather(*(client.put(path, json={**payload, 'revision': 1, 'contribution': update,
                                                                      'outcome': '有测试记录'})
                                              for update in updates))
            assert sorted(attempt.status_code for attempt in attempts) == [200, 409]
            winner = next(attempt.json()['draft'] for attempt in attempts if attempt.status_code == 200)
            assert winner['revision'] == 2 and winner['contribution'] in updates
            assert winner['outcome'] == '有测试记录'
            assert winner['content'] == f"{project.name}\n本人职责：{winner['contribution']}\n依据与结果：有测试记录"
            assert (await client.get(path)).json()['draft'] == winner
            items = {item['id']: item for item in (await client.get('/projects')).json()['items']}
            assert items[project.id]['resume_draft_updated_at'] == winner['updated_at']
            assert items[other_project.id]['resume_draft_updated_at'] is None
            assert foreign_project.id not in items
    finally:
        app.dependency_overrides.pop(get_session)
        await db_session.rollback()
        await db_session.execute(delete(Workspace).where(Workspace.id == foreign_id))
        await db_session.commit()


pytestmark = pytest.mark.usefixtures("enrolled_default_owner")
