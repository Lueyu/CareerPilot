"""Learning feedback: real API/Worker/DB failures; all model output is fixture.

These are not real-user learning or live-model acceptance results.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import socket

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import AppSetting, CompetencySnapshot, Evidence, ScoringRun, ScoringStep, SkillState, Workspace
from learning_process_support import CONFIG, isolated_learning_processes, ready, LearningProcesses, transactional_dump_restore
from test_learning_feedback import task_with_valid_origin


def seed_factory(monkeypatch, *, second_queued=True):
    async def seed(session):
        from app.models.learning_feedback import LearningActivity
        from app.services.learning_feedback import prepare_path, submit_activity
        workspace=Workspace(slug='learning-process-fixture',name='Explicit learning process fixture')
        session.add(workspace);await session.flush()
        # Real API submission resolves its configuration from this DB, unlike
        # the parent's explicitly patched fixture preparation function.
        session.add(AppSetting(key=f'llm:{workspace.id}',value=deepcopy(CONFIG)))
        await session.flush()
        task=await task_with_valid_origin(session,workspace,monkeypatch)
        # The seed helper stubs the origin exam. Queue practice submissions with
        # the same stored configuration the separate API and worker will read.
        from app.services import assessment_jobs, project_autopilot_config
        monkeypatch.setattr(assessment_jobs, 'runtime_config', project_autopilot_config.runtime_config)
        path=await prepare_path(session,workspace,task.id)
        activities=[await session.get(LearningActivity,row['id']) for row in path['activities'][:2]]
        first=await submit_activity(session,workspace,activities[0],
            {'text_answer':'Explicit fixture practice answer one describes the mechanism.','ai_declared':False},
            'process-first',activities[0].version)
        second=await submit_activity(session,workspace,activities[1],
            {'text_answer':'Explicit fixture practice answer two explains a boundary.','ai_declared':False},
            'process-second',activities[1].version) if second_queued else None
        run_id=second.scoring_run_id if second else first.scoring_run_id
        step_id=await session.scalar(select(ScoringStep.id).where(ScoringStep.run_id==run_id))
        return {'workspace_slug':workspace.slug,'workspace_id':workspace.id,'task_id':task.id,
            'goal_id':path['goal']['id'],'activity_id':activities[1].id,'activity_version':activities[1].version,
            'first_submission_id':first.id,'first_run_id':first.scoring_run_id,
            'submission_id':second.id if second else first.id,
            'exam_id':second.exam_id if second else first.exam_id,'run_id':run_id,'slow_step_id':step_id}
    return seed


def project_seed_factory(monkeypatch):
    async def seed(session):
        from app.models import Project,ProjectAnalysis
        from app.api.v1.learning_projects import (project_options,create_project_activity,ProjectActivityIn,
            preview_project,PreviewIn,submit_project,ProjectSubmitIn)
        ids=await seed_factory(monkeypatch,second_queued=False)(session)
        workspace=await session.get(Workspace,ids['workspace_id'])
        project=Project(workspace_id=workspace.id,name='Explicit source snapshot fixture',owner='fixture',repo='learning',
            repo_url='https://github.com/fixture/learning',branch='main',meta={'kind':'github'})
        session.add(project);await session.flush()
        analysis=ProjectAnalysis(project_id=project.id,workspace_id=workspace.id,request_key='fixture-baseline',
            status='COMPLETED',report_status='VALID',coverage_status='COMPLETE',source_revision='a'*40,
            snapshot={'files':[{'path':'app.py','content':'def total(a, b):\n    return a - b\n'}],
                'manifest':{'entries':[{'path':'app.py'}]}},
            report={'issues':[{'id':'I1','title':'核对加法结果','detail':'本测试夹具返回减法结果',
                'citations':[{'path':'app.py','start_line':1,'end_line':2}]}]})
        session.add(analysis);await session.flush();project.latest_valid_analysis_id=analysis.id
        await session.commit()
        choices=await project_options(ids['goal_id'],ws=workspace,session=session)
        made=await create_project_activity(ids['goal_id'],ProjectActivityIn(project_id=project.id,analysis_id=analysis.id,
            option_id=choices['projects'][0]['options'][0]['id'],additional_paths=['test_app.py']),ws=workspace,session=session)
        preview=await preview_project(made['activity_id'],PreviewIn(source_kind='files',base_revision='a'*40,
            files=[{'path':'app.py','content':'def total(a, b):\n    return a + b\n'},
                {'path':'test_app.py','content':'assert total(1, 2) == 3\n'}],
            explanation='显式测试夹具：变更实现并提供断言，未执行项目代码。'),ws=workspace,session=session)
        submitted=await submit_project(made['activity_id'],ProjectSubmitIn(snapshot_id=preview['snapshot_id'],
            request_key='project-restore-fixture',expected_activity_revision=made['version'],assistance={'ai_assisted':True}),
            ws=workspace,session=session)
        step=await session.scalar(select(ScoringStep.id).where(ScoringStep.run_id==submitted['run_id']))
        return {**ids,'activity_id':made['activity_id'],'activity_version':made['version'],
            'project_id':project.id,'analysis_id':analysis.id,'project_snapshot_id':preview['snapshot_id'],
            'submission_id':submitted['submission_id'],'exam_id':submitted['exam_id'],
            'run_id':submitted['run_id'],'slow_step_id':step}
    return seed


async def durable_feedback(fixture,submission_id):
    from app.models.learning_feedback import LearningSubmission
    async with fixture.factory() as session:
        submission=await session.get(LearningSubmission,submission_id)
        run=await session.get(ScoringRun,submission.scoring_run_id)
        step=await session.scalar(select(ScoringStep).where(ScoringStep.run_id==run.id))
        return {'submission_status':submission.status,'run_status':run.status,'mode':run.mode,
            'result':deepcopy(step.result_json),'attempt_count':step.attempt_count,
            'input_hash':step.input_hash,'submission_input_hash':submission.input_hash,
            'assistance':deepcopy(submission.assistance),'activity_version':submission.activity_version}


async def competence_snapshot(fixture):
    async with fixture.factory() as session:
        counts={model.__tablename__:await session.scalar(select(func.count()).select_from(model).where(model.workspace_id==fixture.workspace_id))
                for model in (Evidence,CompetencySnapshot)}
        states=(await session.scalars(select(SkillState).where(SkillState.workspace_id==fixture.workspace_id).order_by(SkillState.id))).all()
        return {**counts,'skill_states':[{col.name:getattr(row,col.name) for col in SkillState.__table__.columns} for row in states]}


@pytest.mark.asyncio
async def test_real_practice_worker_kill_keeps_prior_feedback_and_does_not_add_mastery(tmp_path,monkeypatch):
    async with isolated_learning_processes(tmp_path,seed_factory(monkeypatch)) as (fixture,ids):
        before=await competence_snapshot(fixture)
        (tmp_path/'slow-step-id.txt').write_text(str(ids['slow_step_id']),encoding='ascii')
        first_worker=fixture.start('worker')
        await fixture.until(lambda state:state['steps'][1]['status']=='RUNNING')
        saved=await durable_feedback(fixture,ids['first_submission_id'])
        assert saved['run_status']==saved['submission_status']=='COMPLETED' and saved['mode']=='PRACTICE'
        fixture.stop(first_worker)
        (tmp_path/'release-slow').touch()
        replacement=fixture.start('worker')
        final=await fixture.until(lambda state:state['run_status']=='COMPLETED')
        assert replacement.poll() is None
        assert final['steps'][1]['attempts']==2
        assert any(row['outcome']=='OUTCOME_UNKNOWN' for row in final['steps'][1]['history'])
        assert await durable_feedback(fixture,ids['first_submission_id'])==saved
        assert (await durable_feedback(fixture,ids['submission_id']))['submission_status']=='COMPLETED'
        assert await competence_snapshot(fixture)==before


@pytest.mark.asyncio
async def test_real_http_practice_submit_disconnect_and_api_restart_preserve_one_run(tmp_path,monkeypatch):
    async with isolated_learning_processes(tmp_path,seed_factory(monkeypatch,second_queued=False)) as (fixture,ids):
        before=await competence_snapshot(fixture)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        api=fixture.start('api',port);await ready(api,port)
        headers={'X-API-Token':'learning-process-fixture','X-Workspace':ids['workspace_slug']}
        body={'payload':{'text_answer':'Explicit HTTP fixture answer explains the boundary.','ai_declared':False},
            'request_key':'http-practice-attempt','expected_activity_revision':ids['activity_version']}
        endpoint=f'http://127.0.0.1:{port}/api/v1/learning/activities/{ids["activity_id"]}/submissions'
        async with httpx.AsyncClient(trust_env=False) as client:
            response=await client.post(endpoint,headers=headers,json=body)
            assert response.status_code==202,response.text
            ack=response.json()
        fixture.run_id=ack['scoring_run_id'];fixture.exam_id=ack['exam_id']
        async with fixture.factory() as session:
            step_id=await session.scalar(select(ScoringStep.id).where(ScoringStep.run_id==fixture.run_id))
        (tmp_path/'slow-step-id.txt').write_text(str(step_id),encoding='ascii')
        worker=fixture.start('worker')
        await fixture.until(lambda state:state['steps'][1]['status']=='RUNNING')
        fixture.stop(api)
        restarted=fixture.start('api',port);await ready(restarted,port)
        assert worker.poll() is None
        async with httpx.AsyncClient(trust_env=False) as client:
            repeated=await client.post(endpoint,headers=headers,json=body)
            assert repeated.status_code==202 and repeated.json()['submission_id']==ack['submission_id']
            pending=await client.get(f'http://127.0.0.1:{port}/api/v1/learning/submissions/{ack["submission_id"]}',headers=headers)
            assert pending.status_code==200
        (tmp_path/'release-slow').touch()
        done=await fixture.until(lambda state:state['run_status']=='COMPLETED')
        assert done['steps'][1]['attempts']==1 and worker.poll() is None
        async with httpx.AsyncClient(trust_env=False) as client:
            response=await client.get(f'http://127.0.0.1:{port}/api/v1/learning/submissions/{ack["submission_id"]}',headers=headers)
            assert response.status_code==200
            view=response.json()
            assert view['status']=='COMPLETED' and view['feedback']['correct']
            assert view['feedback']['versions']['rubric_version']
        assert await competence_snapshot(fixture)==before


async def learning_relations(factory):
    async with factory() as session:
        linked=await session.scalar(text('''SELECT count(*) FROM learning_submissions s
            JOIN learning_activities a ON a.id=s.activity_id AND a.workspace_id=s.workspace_id
            JOIN learning_goals g ON g.id=s.goal_id AND g.id=a.goal_id AND g.workspace_id=s.workspace_id
            JOIN learning_tasks t ON t.id=g.source_task_id AND t.workspace_id=g.workspace_id
            JOIN evidence v ON v.id=t.source_evidence_id AND v.workspace_id=t.workspace_id
            JOIN exam_questions origin_q ON origin_q.id=t.source_exam_question_id
            JOIN exam_answer_revisions origin_r ON origin_r.id::text=g.origin_refs->>'answer_revision_id'
              AND origin_r.exam_question_id=origin_q.id
            JOIN exams e ON e.id=s.exam_id AND e.workspace_id=s.workspace_id
            JOIN exam_questions q ON q.exam_id=e.id
            JOIN exam_answer_revisions r ON r.id=s.answer_revision_id AND r.exam_question_id=q.id
            JOIN assessment_scoring_runs run ON run.id=s.scoring_run_id AND run.exam_id=e.id
              AND run.workspace_id=s.workspace_id AND run.mode='PRACTICE'
            JOIN assessment_scoring_steps step ON step.run_id=run.id AND step.exam_question_id=q.id
            WHERE g.origin_refs->>'criterion_id' IS NOT NULL
              AND step.input_json->>'learning_submission_id'=s.id::text
              AND t.source_exam_id=origin_q.exam_id
              AND v.meta->>'exam_question_id'=origin_q.id::text'''))
        completed=await session.scalar(text("SELECT count(*) FROM learning_submissions WHERE status='COMPLETED'"))
        running=await session.scalar(text("SELECT count(*) FROM assessment_scoring_steps s JOIN assessment_scoring_runs r ON r.id=s.run_id WHERE r.mode='PRACTICE' AND s.status='RUNNING'"))
        candidates=await session.scalar(text('''SELECT count(*) FROM memory_items m
            JOIN learning_submissions s ON s.id::text=m.meta->>'submission_id' AND s.workspace_id=m.workspace_id
            JOIN learning_activities a ON a.id=s.activity_id AND a.id::text=m.meta->>'activity_id'
            JOIN learning_goals g ON g.id=s.goal_id AND g.id::text=m.meta->>'goal_id'
            JOIN assessment_scoring_runs r ON r.id=s.scoring_run_id AND r.id::text=m.meta->>'scoring_run_id'
            WHERE m.mtype='LearningObservation' AND m.status='CANDIDATE' AND m.freshness='CURRENT'
              AND m.source_ref='learning-submission:'||s.id::text AND s.status='COMPLETED'
              AND m.meta->>'answer_revision_id'=s.answer_revision_id::text
              AND m.meta->>'submission_revision'=s.revision::text
              AND m.meta->'origin_refs'=g.origin_refs AND m.meta->'assistance'=s.assistance'''))
        return {'linked_goal_activity_submission_revision_scoring_and_source_evidence':linked,
                'completed_submissions':completed,'running_practice_steps':running,
                'linked_pending_memory_candidates':candidates}


@pytest.mark.asyncio
@pytest.mark.parametrize('with_project',[False,True],ids=['concept','project-snapshot'])
async def test_practice_chain_dump_restore_and_real_worker_resume(tmp_path,monkeypatch,with_project):
    from app.services.assessment_versions import digest
    from app.services.assessment_jobs import Claim
    from app.services.assessment_worker import cached_result
    seed=project_seed_factory(monkeypatch) if with_project else seed_factory(monkeypatch)
    async with isolated_learning_processes(tmp_path,seed) as (fixture,ids):
        baseline=await competence_snapshot(fixture)
        (tmp_path/'slow-step-id.txt').write_text(str(ids['slow_step_id']),encoding='ascii')
        worker=fixture.start('worker')
        await fixture.until(lambda state:state['steps'][1]['status']=='RUNNING')
        fixture.stop(worker)
        source_relations=await learning_relations(fixture.factory)
        assert source_relations=={'linked_goal_activity_submission_revision_scoring_and_source_evidence':2,
            'completed_submissions':1,'running_practice_steps':1,'linked_pending_memory_candidates':1}
        saved_feedback=await durable_feedback(fixture,ids['first_submission_id'])
        source_url=make_url(fixture.env['DATABASE_URL'])
        restored_url,report=await transactional_dump_restore(source_url,tmp_path/'dump-restore')
        engine=create_async_engine(restored_url,poolclass=NullPool)
        factory=async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
        restored_folder=tmp_path/'restored-process';restored_folder.mkdir()
        env={**fixture.env,'DATABASE_URL':restored_url.render_as_string(hide_password=False)}
        restored=LearningProcesses(restored_folder,restored_url.database,fixture.exam_id,fixture.run_id,
            fixture.workspace_id,factory,env)
        try:
            assert await learning_relations(factory)==source_relations
            assert await durable_feedback(restored,ids['first_submission_id'])==saved_feedback
            if with_project:
                from app.models.learning_project import LearningProjectSnapshot
                async with factory() as session:
                    snapshot=await session.get(LearningProjectSnapshot,ids['project_snapshot_id'])
                    practice_step=await session.scalar(select(ScoringStep).where(ScoringStep.run_id==ids['run_id']))
                    assert snapshot and len(snapshot.snapshot['files'])==2
                    assert snapshot.snapshot['test_evidence']['execution']=='NOT_EXECUTED'
                    assert practice_step.input_json['learning_activity_kind']=='PROJECT'
                    assert practice_step.input_json['submitted_answer']['text_answer']==snapshot.snapshot['answer_text']
                    assert snapshot.content_hash==snapshot.snapshot['content_hash']
            source_files=list((tmp_path/'private-outbox').glob('*/*.json'))
            assert source_files,'A successful feedback result must have an outbox artifact'
            target_outbox=restored_folder/'private-outbox'/digest(str(restored_url))[:24]
            target_outbox.mkdir(parents=True)
            attachments=[]
            async with factory() as session:
                for source in source_files:
                    payload=json.loads(source.read_text('utf-8'))
                    step=await session.get(ScoringStep,payload['step_id'])
                    run=await session.get(ScoringRun,payload['run_id'])
                    claim=Claim(step.id,run.id,run.exam_id,run.workspace_id,run.generation,
                        'read-only-restore-check',step.fencing_token,step.input_hash,step.input_json)
                    target=target_outbox/source.name
                    shutil.copyfile(source,target)
                    assert target.read_bytes()==source.read_bytes()
                    assert cached_result(target,claim)['points']==step.result_json['points']
                    attachments.append({'name':source.name,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                        'revalidated_against_restored_input':True})
            recovered_worker=restored.start('worker')
            final=await restored.until(lambda state:state['run_status']=='COMPLETED')
            restored.stop(recovered_worker)
            assert final['steps'][1]['attempts']==2
            assert any(item['outcome']=='OUTCOME_UNKNOWN' for item in final['steps'][1]['history'])
            assert await durable_feedback(restored,ids['first_submission_id'])==saved_feedback
            assert await competence_snapshot(restored)==baseline
            after=await learning_relations(factory)
            assert after=={'linked_goal_activity_submission_revision_scoring_and_source_evidence':2,
                'completed_submissions':2,'running_practice_steps':0,'linked_pending_memory_candidates':2}
            assert await learning_relations(fixture.factory)==source_relations
            project_feedback=None
            if with_project:
                feedback=await durable_feedback(restored,ids['submission_id'])
                project_feedback=feedback['result']['project_feedback']
                assert project_feedback['test_verification']=='NOT_EXECUTED'
                assert project_feedback['understanding']=='UNVERIFIED'
                for row in feedback['result']['criterion_scores']:
                    if row['criterion_id'] in ('project_requirements','project_verification'):
                        assert any(quote.startswith('[') and ':L' in quote for quote in row['answer_quotes'])
            report.update(application_recovery_not_yet_checked=False,outbox_attachments_not_yet_checked=False,
                restored_outbox_attachments=attachments,relations_before_restore=source_relations,
                relations_after_resumption=after,prior_feedback_unchanged=True,
                competency_evidence_and_state_unchanged=True,original_source_unchanged_after_restored_worker=True,
                restored_real_worker_pid=recovered_worker.pid,
                restored_step_attempts=final['steps'][1]['attempts'],
                includes_nonempty_project_snapshot=with_project,
                project_snapshot_id=ids.get('project_snapshot_id'),project_feedback=project_feedback,
                project_source_semantics='Synthetic fixture baseline and selected text files; no remote repository call and no project code execution' if with_project else None,
                model_semantics='Explicit fixture; no external model calls or real user progress')
            (tmp_path/'learning-restore-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            for process in restored.processes:restored.stop(process)
            for stream in restored.streams:stream.close()
            await engine.dispose()
