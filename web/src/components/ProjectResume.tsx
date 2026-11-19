import React, { useEffect, useId, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, ApiError, getWorkspace } from '../api/client';

interface ProjectMaterial { id: string; title: string; detail: string }
interface ProjectFact extends ProjectMaterial {
  source: { kind: 'analysis'; revision: string | null; freshness: string };
  basis?: 'code' | 'unclassified';
  citations: { path?: string; file_path?: string; start_line?: number; end_line?: number }[];
}
interface ResumeDraft { revision: number; contribution: string; outcome: string; content: string; updated_at: string }
interface ProjectResumeData { project_id: number; evidence: ProjectFact[]; suggestions: ProjectMaterial[]; profile?: { responsibilities?: string | null; outcomes?: string | null } | null; draft: ResumeDraft | null }
interface Contribution { responsibility: string; outcome: string }
type Screen = { kind: 'materials' } | { kind: 'edit'; fields: Contribution; confirmed: boolean } | { kind: 'preview'; fields: Contribution; text: string; saved: boolean };

const basisLabels = { code: '代码观察', unclassified: '来源类型待核实' };
function draftFields(draft?: ResumeDraft | null): Contribution {
  return { responsibility: draft?.contribution ?? '', outcome: draft?.outcome ?? '' };
}

function formatDraft(name: string, fields: Contribution) {
  return [name, `本人职责：${fields.responsibility.trim()}`, fields.outcome.trim() && `依据与结果：${fields.outcome.trim()}`].filter(Boolean).join('\n');
}

export default function ProjectResume({ projectId }: { projectId: number }) {
  return <ResumeWorkspace key={`${getWorkspace()}:${projectId}`} projectId={projectId} />;
}

function ResumeWorkspace({ projectId }: { projectId: number }) {
  const endpoint = `/projects/${projectId}/resume`;
  const heading = useId();
  const suggestionsId = useId();
  const [data, setData] = useState<ProjectResumeData>();
  const [project, setProject] = useState<{ id: number; name: string }>();
  const [screen, setScreen] = useState<Screen>({ kind: 'materials' });
  const [params] = useSearchParams();
  const openSaved = params.get('draft') === '1';
  const [moreFacts, setMoreFacts] = useState(false);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [error, setError] = useState<{ message: string; conflict?: boolean }>();
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.all([api.get<ProjectResumeData>(endpoint), api.get<{id: number; name: string}>(`/projects/${projectId}`)]).then(([value, metadata]) => {
      if (value.project_id !== projectId || metadata.id !== projectId) throw new Error('收到其他项目的素材，请重新读取。');
      if (active) {
        setData(value);
        setProject(metadata);
        if (openSaved && value.draft) {
          setScreen({ kind: 'preview', fields: draftFields(value.draft), text: value.draft.content, saved: true });
        } else {
          setScreen({ kind: 'materials' });
        }
      }
    }).catch(failure => {
      if (active) setError({ message: failure instanceof Error ? failure.message : '读取失败' });
    });
    return () => { active = false; };
  }, [endpoint, projectId, attempt, openSaved]);

  function reload() {
    setData(undefined);
    setError(undefined);
    setScreen({ kind: 'materials' });
    setAttempt(value => value + 1);
  }

  function showMaterials() {
    setError(undefined);
    setScreen({ kind: 'materials' });
  }

  function showEditor(fields: Contribution) {
    setError(undefined);
    setScreen({ kind: 'edit', fields, confirmed: false });
  }

  async function save(fields: Contribution) {
    if (!data || busy) return;
    setBusy(true);
    setError(undefined);
    try {
      const response = await api.put<{project_id: number; draft: ResumeDraft}>(endpoint, {
        project_id: projectId, revision: data.draft?.revision ?? 0, contribution: fields.responsibility.trim(), outcome: fields.outcome.trim(), confirmed: true,
      });
      if (response.project_id !== projectId) throw new Error('收到其他项目的草稿，请重新读取。');
      setData({ ...data, draft: response.draft });
      setScreen({ kind: 'preview', fields: draftFields(response.draft), text: response.draft.content, saved: true });
    } catch (failure) {
      setError({ message: failure instanceof Error ? failure.message : '保存失败，请重试。', conflict: failure instanceof ApiError && failure.status === 409 });
    } finally { setBusy(false); }
  }

  const backToProjects = <Link className="btn btn-outline" to="/assets">返回项目列表</Link>;
  const message = error && <p className="resume-error" role="alert">{error.message}</p>;

  if (!data || !project) return <section className="project-resume" aria-label="简历素材">
    {error ? <>{message}<div><button type="button" className="btn btn-outline" onClick={reload}>重试读取</button></div></> : <p role="status">正在读取项目简历素材…</p>}
  </section>;

  const savedDraft = data.draft;
  const registeredResponsibility = data.profile?.responsibilities?.trim() ?? '';
  const registeredOutcome = data.profile?.outcomes?.trim() ?? '';

  return <section className="project-resume" aria-labelledby={heading}>
    {screen.kind === 'materials' && <>
      <header>
        <h2 id={heading}>把项目事实整理成个人表达</h2>
        <p>保留材料来源，写入草稿前核对自己的职责。</p>
      </header>
      <div className="resume-card resume-facts">
        <small>项目资料与来源</small>
        {data.evidence.length ? (moreFacts ? data.evidence : data.evidence.slice(0, 3)).map(fact => <div key={fact.id}>
          <h3>{fact.title}</h3>
          {fact.detail && <p className="resume-text">{fact.detail}</p>}
          <p>来源：{basisLabels[fact.basis ?? 'unclassified']}{fact.source.revision && ` · ${fact.source.revision}`}{fact.source.freshness === 'NEEDS_REVIEW' && ' · 仓库版本已变化，需复核'}</p>
          {fact.citations.map((citation, index) => <small className="resume-citation" key={index}>{citation.path ?? citation.file_path}{citation.start_line && `:${citation.start_line}${citation.end_line ? `–${citation.end_line}` : ''}`}</small>)}
        </div>) : <p>暂无可引用的项目资料。仍可补充本人实际参与的内容。</p>}
        {data.evidence.length > 3 && <div><button type="button" className="btn btn-outline" aria-expanded={moreFacts} onClick={() => setMoreFacts(!moreFacts)}>{moreFacts ? '收起资料' : `查看全部 ${data.evidence.length} 条资料`}</button></div>}
      </div>
      <div className="resume-card">
        <small>我的角色</small>
        <h3>本人承担的模块、做过的决定、可证明的结果</h3>
        <p>仓库发现只说明项目情况。待办改进保留在改进清单；草稿仅使用本人确认的内容。</p>
        <div className="resume-actions">
          <button type="button" className="btn btn-outline" aria-expanded={suggestionsOpen} aria-controls={suggestionsId} onClick={() => setSuggestionsOpen(open => !open)}>查看改进清单</button>
          {backToProjects}
        </div>
        <div id={suggestionsId} hidden={!suggestionsOpen}>
          <ul>{data.suggestions.map(suggestion => <li key={suggestion.id}><strong>{suggestion.title}</strong><p>{suggestion.detail}</p></li>)}</ul>
        </div>
      </div>
      <div className="resume-actions">
        <button type="button" className="btn btn-primary" onClick={() => showEditor(draftFields(data.draft))}>{data.draft ? '编辑并重新确认' : '补充本人职责'}</button>
        {savedDraft && <button type="button" className="btn btn-outline" onClick={() => setScreen({ kind: 'preview', fields: draftFields(savedDraft), text: savedDraft.content, saved: true })}>打开已保存草稿</button>}
      </div>
    </>}
    {screen.kind === 'edit' && <>
      <header>
        <h2 id={heading}>核对本人职责</h2>
        <p>填写自己实际参与的内容，保留依据和职责边界。</p>
      </header>
      <form onSubmit={event => {
        event.preventDefault();
        const fields = screen.fields;
        if (screen.confirmed && fields.responsibility.trim()) setScreen({ kind: 'preview', fields, text: formatDraft(project.name, fields), saved: false });
      }}>
        <div className="resume-card">
          <small>职责与取舍</small>
          <label>我负责了哪一段？<textarea required rows={4} maxLength={5000} value={screen.fields.responsibility} onChange={event => setScreen({ ...screen, fields: { ...screen.fields, responsibility: event.currentTarget.value }, confirmed: false })} /></label>
          <p>写清你亲自完成的工作与做过的决定。</p>
          {registeredResponsibility && <div className="resume-actions"><span className="resume-text">已登记职责：{registeredResponsibility}</span><button type="button" className="btn btn-outline" onClick={() => setScreen({ ...screen, fields: { ...screen.fields, responsibility: [screen.fields.responsibility, registeredResponsibility].filter(Boolean).join('\n') }, confirmed: false })}>选入已登记职责</button></div>}
        </div>
        <div className="resume-card">
          <small>依据与结果</small>
          <label>我如何验证？有什么结果？（选填）<textarea rows={3} maxLength={5000} value={screen.fields.outcome} onChange={event => setScreen({ ...screen, fields: { ...screen.fields, outcome: event.currentTarget.value }, confirmed: false })} /></label>
          {registeredOutcome && <div className="resume-actions"><span className="resume-text">已登记依据与结果：{registeredOutcome}</span><button type="button" className="btn btn-outline" onClick={() => setScreen({ ...screen, fields: { ...screen.fields, outcome: [screen.fields.outcome, registeredOutcome].filter(Boolean).join('\n') }, confirmed: false })}>选入已登记依据与结果</button></div>}
          <label className="resume-confirm"><input type="checkbox" checked={screen.confirmed} onChange={event => setScreen({ ...screen, confirmed: event.currentTarget.checked })} /> 我确认以上内容是本人完成且能够说明依据的工作。</label>
          <div className="resume-actions">
            <button type="submit" className="btn btn-primary" disabled={!screen.confirmed || !screen.fields.responsibility.trim() || screen.fields.responsibility.length > 5000 || screen.fields.outcome.length > 5000}>预览本地草稿</button>
            <button type="button" className="btn btn-outline" onClick={showMaterials}>返回素材</button>
          </div>
        </div>
      </form>
    </>}
    {screen.kind === 'preview' && <>
      <header>
        <h2 id={heading}>本地简历草稿</h2>
        <p>{screen.saved ? '已保存到当前项目，可返回后重新打开。' : '当前为预览，保存后可在这个项目里重新打开。'}草稿仅供本地查看。</p>
      </header>
      <div className="resume-card">
        <small>草稿</small>
        <h3>用项目场景讲清自己的贡献</h3>
        <p className="resume-text">{screen.text}</p>
      </div>
      <div className="resume-card">
        <small>来源</small>
        <h3>本人填写并确认的项目贡献</h3>
        {screen.saved && data.draft && <p>保存时间：{new Date(data.draft.updated_at).toLocaleString('zh-CN')}</p>}
        <div className="resume-actions">
          {!screen.saved && <button type="button" className="btn btn-primary" disabled={busy || error?.conflict} onClick={() => save(screen.fields)}>{busy ? '正在保存…' : '保存本地草稿'}</button>}
          <button type="button" className="btn btn-outline" disabled={busy} onClick={() => showEditor(screen.fields)}>返回编辑</button>
          <button type="button" className="btn btn-outline" disabled={busy} onClick={showMaterials}>查看素材</button>
          {backToProjects}
        </div>
      </div>
    </>}
    {message}
    {error?.conflict && <div><button type="button" className="btn btn-outline" onClick={reload}>读取最新版本</button></div>}
  </section>;
}
