"""Lightweight repository checks; persisted due time, no offline catch-up backlog."""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select, text

from app.models import Project, ProjectAnalysis
from app.services.project_autopilot_config import model_fingerprint, runtime_config, settings_for
from app.services.project_autopilot_sources import probe_repository
from app.services.project_context import project_kind
from app.services.project_jobs import OPEN, enqueue, utcnow


def versions():
    from app.services.project_autopilot_engine import PIPELINE_VERSION, PROMPT_BUNDLE_VERSION
    return PIPELINE_VERSION,PROMPT_BUNDLE_VERSION

async def check_project(session,project_id,workspace_id,now=None):
    now=now or utcnow()
    project=await session.scalar(select(Project).where(Project.id==project_id,
        Project.workspace_id==workspace_id,Project.archived_at.is_(None)))
    if not project:raise LookupError('项目不存在')
    if project_kind(project)=='manual':return {'status':'MANUAL_PROJECT','run_id':None}
    try:config,credentials,settings=await runtime_config(session,workspace_id)
    except PermissionError:return {'status':'CONSENT_REQUIRED','run_id':None}
    info={k:getattr(project,k) for k in ('id','owner','repo','branch','repo_url')};info['kind']=project_kind(project)
    # Release all DB transactions before source HTTP, no long-lived locks.
    await session.commit()
    try:head=await probe_repository(info,credentials)
    except Exception as exc:
        await session.refresh(project)
        project.last_checked_at=now;project.next_check_at=now+timedelta(hours=settings.get('check_interval_hours',6))
        project.meta={**(project.meta or {}),'check_error':getattr(exc,'category','SOURCE_UNAVAILABLE')}
        await session.commit()
        return {'status':'SOURCE_UNAVAILABLE','run_id':None}
    await session.execute(text('SELECT pg_advisory_xact_lock(20261117, :id)'),{'id':project_id})
    await session.refresh(project)
    if project.archived_at:return {'status':'REMOVED','run_id':None}
    project.last_checked_at=now;project.next_check_at=now+timedelta(hours=settings.get('check_interval_hours',6))
    project.meta={k:v for k,v in (project.meta or {}).items() if k!='check_error'}
    branch_changed=bool(project.branch and project.branch!=head['branch'])
    project.branch=head['branch']
    baseline=await session.get(ProjectAnalysis,project.latest_valid_analysis_id) if project.latest_valid_analysis_id else None
    pipeline,prompt=versions();fp=model_fingerprint(config)
    from app.services.project_autopilot_engine import SAFETY_POLICY_VERSION, SCHEMA_VERSION
    provenance=((baseline.snapshot or {}).get('provenance') or {}) if baseline else {}
    # The card-layout release adds optional evidence fields and Auto routing.
    # Updating the app alone should not launch a paid rescan of unchanged v2 reports.
    prompt_upgrade = baseline and (baseline.prompt_bundle_version, prompt) == (
        'project-autopilot-prompts-v2', 'project-autopilot-prompts-v3')
    unchanged=(baseline and baseline.workspace_id==workspace_id and baseline.source_revision==head['target_sha'] and
        (prompt_upgrade or (baseline.prompt_bundle_version==prompt and baseline.model_config_fingerprint==fp)) and
        baseline.pipeline_version==pipeline and provenance.get('schema_version')==SCHEMA_VERSION and
        provenance.get('safety_policy_version')==SAFETY_POLICY_VERSION and not branch_changed)
    busy=await session.scalar(select(ProjectAnalysis).where(ProjectAnalysis.project_id==project.id,
        ProjectAnalysis.status.in_(OPEN),ProjectAnalysis.pipeline_version!='legacy').order_by(ProjectAnalysis.id.desc()))
    pending=project.pending_update or {}
    if unchanged and not busy and pending.get('mode')!='full':
        project.pending_update=None;project.last_seen_sha=head['target_sha'];await session.commit()
        return {'status':'UNCHANGED','run_id':None}
    mode='full' if branch_changed or not baseline or pending.get('mode')=='full' else 'incremental'
    await session.flush()
    run=await enqueue(session,project,pending.get('request_key') or uuid4().hex,mode=mode,target_sha=head['target_sha'],
        budget=settings['budget'],model_config_fingerprint=fp,pipeline_version=pipeline,prompt_bundle_version=prompt)
    project.last_seen_sha=head['target_sha']
    if not busy:project.pending_update=None
    await session.commit()
    return {'status':run.status,'run_id':run.id}

async def tick(factory,limit=10):
    now=utcnow()
    async with factory() as session:
        # Claim a due check with a persisted short reservation. Multiple workers
        # don't all poll the same repo, and a crashed check becomes due again.
        rows=(await session.scalars(select(Project).where(Project.archived_at.is_(None),
            or_(Project.next_check_at.is_(None),Project.next_check_at<=now)).order_by(Project.id)
            .limit(limit).with_for_update(skip_locked=True))).all()
        ids=[]
        for p in rows:
            s=await settings_for(session,p.workspace_id)
            p.next_check_at=now+timedelta(minutes=5)
            if s['enabled'] and project_kind(p)!='manual':ids.append((p.id,p.workspace_id))
        await session.commit()
    for pid,wsid in ids:
        async with factory() as session:
            await check_project(session,pid,wsid,now)
