import React, {useEffect, useState} from 'react';
import {Link} from 'react-router-dom';
import {api} from '../api/client';
import {messageOf} from '../lib/questionWorkshop';
import type {AutopilotRun, AutopilotSummary, WorkerStatus, SourceCitation, SourceExcerpt} from '../lib/projectIntelligence';
import SourceCitations from './SourceCitations';
import StudyEntryButton from './StudyEntryButton';
import {ProjectRunStatus} from './ProjectAutopilot';

interface Technology { name:string; use:string; limitations:string | string[]; citations:SourceCitation[]; claim_id?:string }
interface Dossier {
  project_id:number;
  latest_run?:AutopilotRun|null;
  worker?:WorkerStatus;
  practice_questions?:{id:number;short_code:string;title:string;set_id:string;set_title:string}[];
  report:{id:number; summary?:string; source_revision:string|null; freshness:string; as_of:string|null} | null;
  technologies_assessed?:boolean;
  technologies:Technology[];
  bank_questions:{id:number;short_code:string;title:string}[];
  suggested_questions:{id:string;title:string;issue_id?:string;citations:SourceCitation[]}[];
  recommendations:{id:string;title:string;detail:string;impact:string;citations:SourceCitation[]}[];
  limitations:string[];
  source_excerpts:SourceExcerpt[];
}

export default function ProjectDossier({projectId,manual,analysis,onRun}: {projectId:number;manual:boolean;analysis?:AutopilotSummary;onRun?:(run:AutopilotRun|null)=>void}) {
  const [data,setData] = useState<Dossier>();
  const [error,setError] = useState('');
  const [attempt,setAttempt] = useState(0);
  const [expanded,setExpanded] = useState(false);
  const [allTechnology,setAllTechnology] = useState(false);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      try {
        const result = await api.get<Dossier>('/projects/' + projectId + '/dossier');
        if (result.project_id !== projectId || !Array.isArray(result.bank_questions)) throw new Error('项目档案返回的数据不完整');
        if (alive) {
          setData(result); setError('');
          if (result.latest_run !== undefined) onRun?.(result.latest_run);
          if (result.latest_run && ['WAITING_WORKER','QUEUED','RETRY_WAIT','RUNNING'].includes(result.latest_run.status)) timer = setTimeout(read, 6000);
        }
      } catch (e) { if (alive) setError(messageOf(e)); }
    }
    void read();
    return () => { alive = false; clearTimeout(timer); };
  }, [projectId, attempt, onRun]);

  const run = data && data.latest_run !== undefined ? data.latest_run : analysis?.latest_run;
  const practice = data?.practice_questions ?? [];
  const report = data?.report;
  const newerRun = run && report && run.id !== report.id;
  const scanning = run && ['WAITING_WORKER','QUEUED','RETRY_WAIT','RUNNING'].includes(run.status);
  const excerpts = data?.source_excerpts || [];
  const cite = (citations: SourceCitation[]) => citations?.length > 0 && <SourceCitations citations={citations} sourceExcerpts={excerpts}/>;
  return <section aria-label="项目档案" className="dossier">
    {error && <p role="alert">档案读取失败：{error} <button className="btn btn-outline btn-sm" onClick={() => setAttempt(x=>x+1)}>重试</button></p>}
    {!data && !error && <p role="status">正在读取项目档案…</p>}
    {data && <>
      <section className="dossier-lead">
        <div className="dossier-lead-meta"><span className="project-pill">{manual ? '手工项目' : 'AI 扫描'}</span>
          {report?.source_revision && <code title={report.source_revision}>{report.source_revision.slice(0,12)}</code>}
          {report && <small>{newerRun ? '此前有效报告' : '报告'} #{report.id}{report.freshness === 'NEEDS_REVIEW' ? ' · 代码版本已更新' : report.freshness === 'UNKNOWN' ? ' · 版本新鲜度待核实' : ''}</small>}
        </div>
        <h2>{manual ? '我的项目经历' : report ? '项目解读' : scanning ? '正在准备项目报告' : '从一次 AI 扫描开始'}</h2>
        <p>{manual ? '整理本人职责与项目追问，可直接用于面试准备。' : report?.summary || (report ? '本次报告的观察与引用见下方。' : '扫描仓库后，这里会展示实际用到的技术、对应代码和可补强的方向。')}</p>
        <div className="dossier-shortcuts">
          {!manual && <Link to="?tab=process">分析过程 ↗</Link>}
          {report && <Link to="?tab=evidence">代码依据 ↗</Link>}

        </div>
      </section>
      {run && run.status !== 'COMPLETED' && <div className="dossier-run"><ProjectRunStatus run={run} worker={data.worker || analysis?.worker} compact/>
        <Link to="?tab=process">查看本次任务和已保存的部分结果</Link></div>}
      <div className="dossier-grid">
        <section className="dossier-card dossier-tech">
          <header><h2>已用技术</h2><span className="dossier-count">{data.technologies.length}</span></header>
          {(allTechnology ? data.technologies : data.technologies.slice(0, 2)).map((t,i)=><article key={t.name + i}><h3>{t.name}</h3><p>{t.use}</p>{t.limitations?.length > 0 && <p className="dossier-muted">{Array.isArray(t.limitations)?t.limitations.join('；'):t.limitations}</p>}{cite(t.citations)}</article>)}
          {data.technologies.length>2 && <button className="btn btn-outline btn-sm" onClick={()=>setAllTechnology(!allTechnology)}>{allTechnology?'收起技术':'全部 '+data.technologies.length+' 项技术'}</button>}
          {!data.technologies.length && <p>{manual ? '手工项目没有可核对的源码。' : !report ? '等待扫描提供源码证据。' : data.technologies_assessed ? '本次已读代码未确认可列出的技术实践。' : '这份报告未单列技术实践。重新扫描可提取有代码依据的技术。'}</p>}
          {report && <Link to="?tab=evidence">查看报告依据 →</Link>}
        </section>
        <section className="dossier-card dossier-questions">
          <header><h2>项目面试题</h2><span className="dossier-count">{data.bank_questions.length + practice.length}</span></header>
          {data.bank_questions.length ? <><small>已入题库</small>{(expanded ? data.bank_questions : data.bank_questions.slice(0,3)).map(q=><article key={q.id}><small>{q.short_code}</small><h3>{q.title}</h3><StudyEntryButton kind="question" id={q.id}/></article>)}
            {data.bank_questions.length>3 && <button className="btn btn-outline btn-sm" onClick={()=>setExpanded(!expanded)}>{expanded?'收起':'查看全部 '+data.bank_questions.length+' 道题'}</button>}</> : <p>还没有已入库并关联这个项目的题目。</p>}
          {practice.length>0 && <details><summary>项目面经练习 · {practice.length}</summary>{practice.map(q=><article key={q.id}><h3>{q.title}</h3><Link to={'/bank?set=' + encodeURIComponent(q.set_id)}>打开面经集 · {q.set_title}</Link></article>)}</details>}
          {data.suggested_questions.length>0 && <details><summary>AI 建议追问 · {data.suggested_questions.length}</summary>{data.suggested_questions.map((q,i)=><article key={q.id || i}><h3>{q.title}</h3>{cite(q.citations)}</article>)}</details>}
          <Link to={'/bank/workshop?project='+projectId}>准备项目面试题 →</Link>
        </section>
        <section className="dossier-card dossier-next">
          <header><h2>竞争力补强</h2><span className="dossier-count">{data.recommendations.length}</span></header>
          {data.recommendations.slice(0,1).map((r,i)=><article key={r.id || i}><h3>{r.title}</h3><p>{r.detail}</p>{r.impact && <p className="dossier-muted">{r.impact}</p>}{cite(r.citations)}</article>)}
          {!data.recommendations.length && <p>{report ? '本次报告没有给出可追溯的补强建议。' : '有报告后，可在这里查看值得补充的实践与验证方法。'}</p>}
          {report && <Link to="?tab=improvements">全部建议与依据 →</Link>}
        </section>
      </div>
      <section className="dossier-resume-strip"><div><h2>简历素材</h2><p>核对个人职责和成果依据后，可保存为这个项目的简历草稿。</p></div><Link className="btn btn-outline" to="?tab=resume">核对简历素材</Link></section>
      {!!data.limitations.length && <details className="dossier-limits"><summary>分析范围与待核实事项</summary><ul>{data.limitations.map((item,index)=><li key={index}>{item}</li>)}</ul></details>}
    </>}
  </section>;
}
