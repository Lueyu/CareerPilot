"""Versioned proposals; report publication never implies personal-memory consent."""
from copy import deepcopy

from sqlalchemy import select

from app.models import MemoryItem, Project
from app.services.bank_review_memory import BANK_REVIEW_FEEDBACK_TYPE
from app.services.project_jobs import fingerprint, utcnow
from app.services.project_memory import PROJECT_TYPES, lock_project_memories, source_excerpts
from app.services.task_memory import TASK_COMPLETION_TYPE
from app.services.tracing import emit_event


def eligible(row):
    return row.mtype not in (TASK_COMPLETION_TYPE, BANK_REVIEW_FEEDBACK_TYPE) and row.status=='ACTIVE' and row.freshness=='CURRENT' and (
        row.mtype not in (*PROJECT_TYPES, 'LearningObservation', 'LearningPreference') or row.confirmed_at is not None)

def view(row):
    meta=row.meta or {}
    return {'id':row.id,'revision':row.revision,'status':row.status,'project_id':meta.get('project_id'),
        'report_id':meta.get('analysis_id'),'base_memory_id':meta.get('base_memory_id'),
        'base_memory_revision':meta.get('base_memory_revision'),'kind':meta.get('kind'),
        'fact_type':meta.get('fact_type','UNASSESSED'),'content':row.content,'freshness':row.freshness,
        'citations':meta.get('citations',[]),'source_excerpts':meta.get('excerpts',[]),
        'change_reason':'来源已变化，请等待新报告后核对；本条不能确认生效' if row.freshness!='CURRENT' else meta.get('change_reason','首次项目观察，等待确认'),
        'suggested_use':'确认后用于项目背景和项目问题出题；不作为个人能力或贡献证据。'}

async def publish_candidates(session,run,proposals):
    if run.report_status!='VALID' or run.coverage_status!='COMPLETE':
        raise ValueError('只有完整有效报告可以生成待确认记忆')
    await lock_project_memories(session,run.workspace_id)
    project=await session.scalar(select(Project).where(Project.id==run.project_id,
        Project.workspace_id==run.workspace_id).with_for_update())
    if project.archived_at or project.latest_valid_analysis_id!=run.id:
        return []
    previous=(await session.scalars(select(MemoryItem).where(MemoryItem.workspace_id==run.workspace_id,
        MemoryItem.mtype.in_(PROJECT_TYPES),MemoryItem.meta['project_id'].as_integer()==run.project_id))).all()
    claims={c.get('id'):c for c in (run.report or {}).get('claims',[])}
    issues={i['id']:i for i in (run.report or {}).get('issues',[])}
    created=[]
    for proposal in proposals:
        ids=proposal.get('claim_ids',[])
        if any(cid not in claims for cid in ids):raise ValueError('候选引用了未验证声明')
        citations=[deepcopy(c) for cid in ids for c in claims[cid].get('citations',[])]
        if not citations:citations=deepcopy(proposal.get('citations',[]))
        excerpts=source_excerpts(run,citations)
        fact_key=proposal['fact_key']
        # Compare actual evidence text, not a new commit ID alone.
        digest=fingerprint({'fact_key':fact_key,'content':proposal['content'],
            'evidence':[{'path':x['path'],'excerpt':x['excerpt']} for x in excerpts]})
        matching=[m for m in previous if (m.meta or {}).get('fact_key')==fact_key]
        same=next((m for m in matching if m.meta.get('proposal_fingerprint')==digest and m.freshness=='CURRENT' and
               (m.status in ('DISMISSED','ACTIVE') or
                (m.status=='PENDING_REVIEW' and m.meta.get('analysis_id')==run.id))),None)
        if same:
            if same.status=='ACTIVE':
                compatible=dict(same.meta.get('compatible_reports') or {})
                compatible[str(run.id)]={'issue_id':proposal.get('issue_id'),
                    'issue':issues.get(proposal.get('issue_id'),{}),'target_sha':run.source_revision}
                same.meta={**same.meta,'compatible_reports':compatible}
            continue
        base=next((m for m in reversed(matching) if m.status=='ACTIVE'),None)
        for old in matching:
            if old.status=='PENDING_REVIEW':old.status='SUPERSEDED';old.revision+=1
        meta={'project_id':run.project_id,'analysis_id':run.id,'source_revision':run.source_revision,
            'scope':'repository_observation','kind':proposal.get('kind'),'fact_key':fact_key,
            'proposal_fingerprint':digest,'claim_ids':ids,'fact_type':proposal.get('fact_type','UNASSESSED'),
            'citations':citations,'excerpts':excerpts,'issue_id':proposal.get('issue_id'),
            'issue':issues.get(proposal.get('issue_id'),{}),'base_memory_id':base.id if base else None,
            'base_memory_revision':base.revision if base else None,
            'change_reason':'新版本证据或结论变化；保留原记忆直到确认' if matching else '首次项目观察，等待确认'}
        row=MemoryItem(workspace_id=run.workspace_id,mtype=proposal.get('mtype','ProjectUnderstanding'),
            content=proposal['content'],source='SYSTEM',source_ref=f'project-analysis:{run.id}',
            status='PENDING_REVIEW',meta=meta,freshness='CURRENT',revision=1)
        if row.mtype not in PROJECT_TYPES:raise ValueError('项目候选不能创建个人能力记忆')
        session.add(row);created.append(row)
    await session.flush()
    return created

async def review_candidates(session,workspace_id,items):
    await lock_project_memories(session,workspace_id)
    result=[]
    for item in items:
        row=await session.scalar(select(MemoryItem).where(MemoryItem.id==item['candidate_id'],
            MemoryItem.workspace_id==workspace_id,MemoryItem.mtype.in_(PROJECT_TYPES)).with_for_update()
            .execution_options(populate_existing=True))
        if row is None:
            result.append({'id':item['candidate_id'],'status':'NOT_FOUND','error':'候选不存在'});continue
        meta=row.meta or {}
        project=await session.scalar(select(Project).where(Project.id==meta.get('project_id'),Project.workspace_id==workspace_id))
        base=await session.scalar(select(MemoryItem).where(MemoryItem.id==meta.get('base_memory_id'),
            MemoryItem.workspace_id==workspace_id).with_for_update()) if meta.get('base_memory_id') else None
        legacy=bool(meta.get('legacy_auto_activated'))
        confirming=item['action']=='confirm'
        competing=None
        if confirming and meta.get('fact_key'):
            competing=await session.scalar(select(MemoryItem.id).where(MemoryItem.workspace_id==workspace_id,
                MemoryItem.meta['project_id'].as_integer()==meta.get('project_id'),
                MemoryItem.meta['fact_key'].as_string()==meta['fact_key'],MemoryItem.status=='ACTIVE',
                MemoryItem.id!=row.id).with_for_update().limit(1))
        conflict=(row.revision!=item['expected_revision'] or row.status!='PENDING_REVIEW' or (confirming and row.freshness!='CURRENT') or not project or
            project.archived_at is not None or (confirming and not legacy and project.latest_valid_analysis_id!=meta.get('analysis_id')) or
            (competing is not None and (base is None or base.id!=competing)) or
            (confirming and base is not None and (base.revision!=meta.get('base_memory_revision') or base.status!='ACTIVE')))
        if conflict:
            result.append({'id':row.id,'status':'CONFLICT','revision':row.revision,'error':'候选、来源报告或原记忆已更新，请重新查看'});continue
        action=item['action']
        if action not in ('confirm','ignore'):
            result.append({'id':row.id,'status':'INVALID','error':'无效审核操作'});continue
        if action=='confirm':
            edited=item.get('edited_content')
            if edited is not None:
                if not edited.strip():
                    result.append({'id':row.id,'status':'INVALID','error':'内容不能为空'});continue
                meta={**meta,'proposed_content':row.content};row.content=edited.strip();row.source='USER';row.meta=meta
            row.status='ACTIVE';row.confirmed_at=utcnow();row.freshness='CURRENT'
            if base:base.status='SUPERSEDED';base.superseded_by=row.id;base.revision+=1
        else:row.status='DISMISSED'
        row.revision+=1
        await emit_event(session,workspace_id,'project.memory.review',{'action':action,'report_id':meta.get('analysis_id'),
            'revision':row.revision,'edited':item.get('edited_content') is not None},'memory',row.id)
        result.append({'id':row.id,'status':'CONFIRMED' if action=='confirm' else 'IGNORED','revision':row.revision})
    await session.commit()
    return result

async def invalidate_changed_sources(session,project,changed_paths,target_sha):
    await lock_project_memories(session,project.workspace_id)
    rows=(await session.scalars(select(MemoryItem).where(MemoryItem.workspace_id==project.workspace_id,
        MemoryItem.mtype.in_(PROJECT_TYPES),MemoryItem.status.in_(('ACTIVE','PENDING_REVIEW')),
        MemoryItem.meta['project_id'].as_integer()==project.id).with_for_update())).all()
    for row in rows:
        citations=(row.meta or {}).get('citations',[])
        if not citations or changed_paths is None or any(c['path'] in changed_paths for c in citations):
            if row.freshness!='NEEDS_REVIEW':row.revision+=1
            row.freshness='NEEDS_REVIEW'
            row.meta={**(row.meta or {}),'review_target_sha':target_sha}
        else:
            row.meta={**(row.meta or {}),'compatible_target_sha':target_sha}
