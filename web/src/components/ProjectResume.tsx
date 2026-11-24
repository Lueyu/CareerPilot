import {useLocale, useT} from '../i18n';
import React, { useEffect, useId, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {ProjectCopyError} from '../lib/projectIntelligence';
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
type Screen = { kind: 'materials' } | { kind: 'edit'; fields: Contribution; confirmed: boolean } | { kind: 'preview'; fields: Contribution; draft?: ResumeDraft };

const basisLabels = { code: 'projects.codeObservation', unclassified: 'projects.unclassifiedBasis' };
function draftFields(draft?: ResumeDraft | null): Contribution {
  return { responsibility: draft?.contribution ?? '', outcome: draft?.outcome ?? '' };
}

function formatDraft(name: string, fields: Contribution, t: ReturnType<typeof useT>) {
  return [name, t('projects.resumeDraftResponsibilities', {text: fields.responsibility.trim()}), fields.outcome.trim() && t('projects.resumeDraftOutcome', {text: fields.outcome.trim()})].filter(Boolean).join('\n');
}

function displaySavedDraft(draft: ResumeDraft, name: string, locale: ReturnType<typeof useLocale>, t: ReturnType<typeof useT>) {
  if (locale === 'zh-CN') return draft.content;
  // Match the API's generated draft before localizing its headings.
  const storedBody = ['本人职责：' + draft.contribution, draft.outcome && '依据与结果：' + draft.outcome].filter(Boolean).join('\n');
  return draft.content === name + '\n' + storedBody ? formatDraft(name, draftFields(draft), t) : draft.content;
}

export default function ProjectResume({ projectId }: { projectId: number }) {
  return <ResumeWorkspace key={`${getWorkspace()}:${projectId}`} projectId={projectId} />;
}

function ResumeWorkspace({ projectId }: { projectId: number }) {
  const t = useT();
  const locale = useLocale();
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
  const [error, setError] = useState<{ detail: unknown; fallbackKey: string; conflict?: boolean }>();
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.all([api.get<ProjectResumeData>(endpoint), api.get<{id: number; name: string}>(`/projects/${projectId}`)]).then(([value, metadata]) => {
      if (value.project_id !== projectId || metadata.id !== projectId) throw new ProjectCopyError('projects.wrongResumeMaterial');
      if (active) {
        setData(value);
        setProject(metadata);
        if (openSaved && value.draft) {
          setScreen({ kind: 'preview', fields: draftFields(value.draft), draft: value.draft });
        } else {
          setScreen({ kind: 'materials' });
        }
      }
    }).catch(failure => {
      if (active) setError({ detail: failure, fallbackKey: 'projects.failedReads' });
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
      if (response.project_id !== projectId) throw new ProjectCopyError('projects.wrongResumeDraft');
      setData({ ...data, draft: response.draft });
      setScreen({ kind: 'preview', fields: draftFields(response.draft), draft: response.draft });
    } catch (failure) {
      setError({ detail: failure, fallbackKey: 'projects.resumeSaveFailed', conflict: failure instanceof ApiError && failure.status === 409 });
    } finally { setBusy(false); }
  }

  const backToProjects = <Link className="btn btn-outline" to="/assets">{t('projects.backToProjectList')}</Link>;
  const message = error && <p className="resume-error" role="alert">{error.detail instanceof ProjectCopyError ? t(error.detail.localeKey) : error.detail instanceof Error ? error.detail.message : t(error.fallbackKey)}</p>;

  if (!data || !project) return <section className="project-resume" aria-label={t('projects.resumeMaterial')}>
    {error ? <>{message}<div><button type="button" className="btn btn-outline" onClick={reload}>{t('projects.retryRead')}</button></div></> : <p role="status">{t('projects.loadingResumeMaterial')}</p>}
  </section>;

  const savedDraft = data.draft;
  const registeredResponsibility = data.profile?.responsibilities?.trim() ?? '';
  const registeredOutcome = data.profile?.outcomes?.trim() ?? '';

  return <section className="project-resume" aria-labelledby={heading}>
    {screen.kind === 'materials' && <>
      <header>
        <h2 id={heading}>{t('projects.turnFactsIntoResume')}</h2>
        <p>{t('projects.keepResumeSources')}</p>
      </header>
      <div className="resume-card resume-facts">
        <small>{t('projects.projectEvidenceAndSources')}</small>
        {data.evidence.length ? (moreFacts ? data.evidence : data.evidence.slice(0, 3)).map(fact => <div key={fact.id}>
          <h3>{fact.title}</h3>
          {fact.detail && <p className="resume-text">{fact.detail}</p>}
          <p>{t('projects.resumeEvidenceSource', {label: t(basisLabels[fact.basis ?? 'unclassified'])})}{fact.source.revision && ` · ${fact.source.revision}`}{fact.source.freshness === 'NEEDS_REVIEW' && t('projects.resumeSourceNeedsReview')}</p>
          {fact.citations.map((citation, index) => <small className="resume-citation" key={index}>{citation.path ?? citation.file_path}{citation.start_line && `:${citation.start_line}${citation.end_line ? `–${citation.end_line}` : ''}`}</small>)}
        </div>) : <p>{t('projects.noResumeEvidence')}</p>}
        {data.evidence.length > 3 && <div><button type="button" className="btn btn-outline" aria-expanded={moreFacts} onClick={() => setMoreFacts(!moreFacts)}>{moreFacts ? t('projects.collapseEvidence') : t('projects.showAllResumeEvidence', {count: data.evidence.length})}</button></div>}
      </div>
      <div className="resume-card">
        <small>{t('projects.myRole')}</small>
        <h3>{t('projects.personalContributionEvidence')}</h3>
        <p>{t('projects.resumeEvidenceBoundary')}</p>
        <div className="resume-actions">
          <button type="button" className="btn btn-outline" aria-expanded={suggestionsOpen} aria-controls={suggestionsId} onClick={() => setSuggestionsOpen(open => !open)}>{t('projects.viewImprovementList')}</button>
          {backToProjects}
        </div>
        <div id={suggestionsId} hidden={!suggestionsOpen}>
          <ul>{data.suggestions.map(suggestion => <li key={suggestion.id}><strong>{suggestion.title}</strong><p>{suggestion.detail}</p></li>)}</ul>
        </div>
      </div>
      <div className="resume-actions">
        <button type="button" className="btn btn-primary" onClick={() => showEditor(draftFields(data.draft))}>{data.draft ? t('projects.editReconfirm') : t('projects.addResponsibilities')}</button>
        {savedDraft && <button type="button" className="btn btn-outline" onClick={() => setScreen({ kind: 'preview', fields: draftFields(savedDraft), draft: savedDraft })}>{t('projects.openSavedDraft')}</button>}
      </div>
    </>}
    {screen.kind === 'edit' && <>
      <header>
        <h2 id={heading}>{t('projects.reviewResponsibilities')}</h2>
        <p>{t('projects.describeYourContribution')}</p>
      </header>
      <form onSubmit={event => {
        event.preventDefault();
        const fields = screen.fields;
        if (screen.confirmed && fields.responsibility.trim()) setScreen({ kind: 'preview', fields });
      }}>
        <div className="resume-card">
          <small>{t('projects.responsibilitiesTradeoffs')}</small>
          <label>{t('projects.whichWorkWasMine')}<textarea required rows={4} maxLength={5000} value={screen.fields.responsibility} onChange={event => setScreen({ ...screen, fields: { ...screen.fields, responsibility: event.currentTarget.value }, confirmed: false })} /></label>
          <p>{t('projects.describeYourDecisions')}</p>
          {registeredResponsibility && <div className="resume-actions"><span className="resume-text">{t('projects.recordedResponsibilities', {text: registeredResponsibility})}</span><button type="button" className="btn btn-outline" onClick={() => setScreen({ ...screen, fields: { ...screen.fields, responsibility: [screen.fields.responsibility, registeredResponsibility].filter(Boolean).join('\n') }, confirmed: false })}>{t('projects.useRegisteredResponsibilities')}</button></div>}
        </div>
        <div className="resume-card">
          <small>{t('projects.evidenceAndResults')}</small>
          <label>{t('projects.howIValidated')}<textarea rows={3} maxLength={5000} value={screen.fields.outcome} onChange={event => setScreen({ ...screen, fields: { ...screen.fields, outcome: event.currentTarget.value }, confirmed: false })} /></label>
          {registeredOutcome && <div className="resume-actions"><span className="resume-text">{t('projects.recordedOutcome', {text: registeredOutcome})}</span><button type="button" className="btn btn-outline" onClick={() => setScreen({ ...screen, fields: { ...screen.fields, outcome: [screen.fields.outcome, registeredOutcome].filter(Boolean).join('\n') }, confirmed: false })}>{t('projects.useRegisteredOutcome')}</button></div>}
          <label className="resume-confirm"><input type="checkbox" checked={screen.confirmed} onChange={event => setScreen({ ...screen, confirmed: event.currentTarget.checked })} /> {t('projects.confirmOwnWork')}</label>
          <div className="resume-actions">
            <button type="submit" className="btn btn-primary" disabled={!screen.confirmed || !screen.fields.responsibility.trim() || screen.fields.responsibility.length > 5000 || screen.fields.outcome.length > 5000}>{t('projects.previewLocalDraft')}</button>
            <button type="button" className="btn btn-outline" onClick={showMaterials}>{t('projects.backToMaterials')}</button>
          </div>
        </div>
      </form>
    </>}
    {screen.kind === 'preview' && <>
      <header>
        <h2 id={heading}>{t('projects.localResumeDraft')}</h2>
        <p>{t(screen.draft ? 'projects.resumeDraftSavedNotice' : 'projects.resumeDraftPreviewNotice')}</p>
      </header>
      <div className="resume-card">
        <small>{t('projects.draft')}</small>
        <h3>{t('projects.showContribution')}</h3>
        <p className="resume-text">{screen.draft ? displaySavedDraft(screen.draft, project.name, locale, t) : formatDraft(project.name, screen.fields, t)}</p>
      </div>
      <div className="resume-card">
        <small>{t('projects.source')}</small>
        <h3>{t('projects.confirmedContribution')}</h3>
        {screen.draft && <p>{t('projects.draftSaveTime', {date: new Date(screen.draft.updated_at).toLocaleString(locale)})}</p>}
        <div className="resume-actions">
          {!screen.draft && <button type="button" className="btn btn-primary" disabled={busy || error?.conflict} onClick={() => save(screen.fields)}>{busy ? t('projects.savingDraft') : t('projects.saveLocalDraft')}</button>}
          <button type="button" className="btn btn-outline" disabled={busy} onClick={() => showEditor(screen.fields)}>{t('projects.backToEdit')}</button>
          <button type="button" className="btn btn-outline" disabled={busy} onClick={showMaterials}>{t('projects.viewMaterials')}</button>
          {backToProjects}
        </div>
      </div>
    </>}
    {message}
    {error?.conflict && <div><button type="button" className="btn btn-outline" onClick={reload}>{t('projects.loadLatestDraft')}</button></div>}
  </section>;
}
