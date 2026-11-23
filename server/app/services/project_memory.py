"""Versioned project observations, kept separate from personal performance."""
from copy import deepcopy

from sqlalchemy import or_, select, text

from app.models import MemoryItem, Project, ProjectAnalysis

PROJECT_TYPES = ('ProjectUnderstanding', 'ProjectIssue')


async def lock_project_memories(session, workspace_id):
    # Same transaction lock as question generation: a frozen prompt sees one
    # coherent version while publication/correction/dismissal occurs atomically.
    await session.execute(text('SELECT pg_advisory_xact_lock(20261116, :ws)'), {'ws': workspace_id})


async def active_project_memories(session, workspace_id, project_id):
    return (await session.scalars(select(MemoryItem).where(
        MemoryItem.workspace_id == workspace_id, MemoryItem.mtype.in_(PROJECT_TYPES),
        MemoryItem.status == 'ACTIVE', MemoryItem.confirmed_at.is_not(None), MemoryItem.freshness == 'CURRENT',
        MemoryItem.meta['project_id'].as_integer() == project_id,
    ).order_by(MemoryItem.id))).all()


def source_excerpts(run, citations):
    files = {item['path']: item['content'].splitlines() for item in (run.snapshot or {}).get('files', [])}
    registry = (run.snapshot or {}).get('source_registry') or {}
    excerpts = []
    for citation in citations:
        source = registry.get(citation.get('source_id')) if citation.get('source_id') else None
        if citation.get('source_id'):
            valid = source and source.get('path')==citation['path'] and (
                not citation.get('source_version') or source.get('source_revision')==citation['source_version'])
            lines = source.get('content','').splitlines() if valid else []
        else:
            lines = files.get(citation['path'], [])  # legacy report, explicitly no source registry identity
        start, end = citation['start_line'], citation['end_line']
        excerpt_end = min(end, start + 39)
        excerpt = '\n'.join(f'{n}: {lines[n-1]}' for n in range(start, min(excerpt_end, len(lines)) + 1))
        excerpts.append({**citation, 'excerpt': excerpt[:10000],
                         'excerpt_truncated': excerpt_end < end or len(excerpt) > 10000})
    return excerpts


async def publish_project_memories(session, run):
    await lock_project_memories(session, run.workspace_id)
    existing = (await session.scalars(select(MemoryItem).where(
        MemoryItem.workspace_id == run.workspace_id,
        MemoryItem.meta['analysis_id'].as_integer() == run.id,
        MemoryItem.mtype.in_(PROJECT_TYPES),
    ))).all()
    if existing:
        return existing
    report = run.report
    base = {'project_id': run.project_id, 'analysis_id': run.id, 'source_revision': run.source_revision,
            'scope': 'repository_observation', 'legacy_auto_activated': True,
            'note': '历史分析候选；项目观察不代表个人贡献或个人能力。'}
    entries = [("ProjectUnderstanding", report['summary'], {**base, 'kind': 'summary', 'citations': []})]
    for section in report['architecture']:
        entries.append(('ProjectUnderstanding', section['title'] + '\n' + section['detail'],
                        {**base, 'kind': 'architecture', 'citations': deepcopy(section['citations'])}))
    for issue in report['issues']:
        content = '\n'.join([issue['title'], issue['detail'], '影响：' + issue['impact'],
                             '建议：' + issue['recommendation'], '判断性质：' + issue['status']])
        entries.append(('ProjectIssue', content, {**base, 'kind': 'issue', 'issue_id': issue['id'],
                        'citations': deepcopy(issue['citations']), 'issue': deepcopy(issue)}))
    if report['unknowns']:
        entries.append(('ProjectUnderstanding', '尚未验证：\n' + '\n'.join(report['unknowns']),
                        {**base, 'kind': 'unknowns', 'citations': []}))
    created = []
    for mtype, content, meta in entries:
        meta['excerpts'] = source_excerpts(run, meta['citations'])
        row = MemoryItem(workspace_id=run.workspace_id, mtype=mtype, content=content,
                         source='SYSTEM', source_ref=f'project-analysis:{run.id}', status='PENDING_REVIEW', meta=meta)
        session.add(row)
        created.append(row)
    await session.flush()
    return created


def memory_fact(row):
    meta = row.meta or {}
    return {'id': f'P{meta["project_id"]}-memory-{row.id}', 'text': row.content,
            'source': 'repository_observation', 'memory_id': row.id,
            'analysis_id': meta.get('analysis_id'), 'source_revision': meta.get('source_revision'),
            'citations': meta.get('citations', []), 'kind': meta.get('kind'),
            'excerpts': meta.get('excerpts', []),
            'issue_id': meta.get('issue_id'), 'user_corrected': row.source == 'USER',
            'note': 'AI 项目观察或用户对项目的纠正；原版本见 source_revision，不是个人掌握证据。'}


def report_binding(row,analysis_id):
    meta=row.meta or {}
    if meta.get('analysis_id')==analysis_id:
        return {'issue_id':meta.get('issue_id'),'issue':meta.get('issue',{})}
    return (meta.get('compatible_reports') or {}).get(str(analysis_id))


async def issue_targets(session, workspace_id, project_ids, analysis_id, issue_ids):
    if analysis_id is None:
        if issue_ids:
            raise ValueError('请选择项目分析版本')
        return []
    if not issue_ids or len(set(issue_ids)) != len(issue_ids):
        raise ValueError('请选择不重复的具体项目问题')
    run = await session.scalar(select(ProjectAnalysis).where(
        ProjectAnalysis.id == analysis_id, ProjectAnalysis.workspace_id == workspace_id,
        ProjectAnalysis.project_id.in_(project_ids),
        or_(ProjectAnalysis.report_status == 'VALID',
            (ProjectAnalysis.pipeline_version == 'legacy') & (ProjectAnalysis.status == 'SUCCESS')),
    ))
    if run is None:
        raise ValueError('项目分析不存在、不属于所选项目或尚未成功')
    project = await session.scalar(select(Project).where(Project.id==run.project_id,
        Project.workspace_id==workspace_id,Project.archived_at.is_(None)))
    if project is None:
        raise ValueError('项目已移除，历史结果不可用于新出题')
    latest = project.latest_valid_analysis_id or await session.scalar(select(ProjectAnalysis.id).where(
        ProjectAnalysis.project_id == run.project_id, ProjectAnalysis.workspace_id == workspace_id,
        ProjectAnalysis.status == 'SUCCESS').order_by(ProjectAnalysis.id.desc()).limit(1))
    if latest != run.id:
        raise ValueError('该分析已被新版本替代，请使用最新成功分析')
    rows = await active_project_memories(session, workspace_id, run.project_id)
    by_issue = {report_binding(m,run.id)['issue_id']: m for m in rows if report_binding(m,run.id) and m.mtype=='ProjectIssue'}
    if any(key not in by_issue for key in issue_ids):
        raise ValueError('所选项目问题不存在或对应记忆已删除/替代，请重新选择')
    return [{'project_id': run.project_id, 'analysis_id': run.id, 'source_revision': run.source_revision,
             'issue_id': key, 'memory_id': by_issue[key].id, 'fact_ref': memory_fact(by_issue[key])['id'],
             'content': by_issue[key].content, 'skill_key': report_binding(by_issue[key],run.id)['issue']['skill_key'],
             'memory_source_revision':by_issue[key].meta.get('source_revision'),
             'memory_analysis_id':by_issue[key].meta.get('analysis_id'),
             'citations': by_issue[key].meta.get('citations', []),
             'excerpts': by_issue[key].meta.get('excerpts', []),
             'assessment_goal': '先解释项目现状与取舍，再分析风险和改进；个人短板以实际作答评分为准。'}
            for key in issue_ids]
