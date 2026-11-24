import {useT} from '../i18n';
import React, {useEffect, useRef, useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {api} from '../api/client';
import {Modal} from './ui';
import {projectProviderName, type AutopilotSettings, type AutopilotSummary} from '../lib/projectIntelligence';
import {messageOf} from '../lib/questionWorkshop';

interface Props {
  projectId: number;
  name: string;
  analysis?: AutopilotSummary;
  repository?:string;
  branch?:string;
  onQueued?: () => void;
}
export default function ProjectScanButton({projectId, name, analysis, onQueued, repository, branch}: Props) {
  const t = useT();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [settings, setSettings] = useState<AutopilotSettings>();
  useEffect(() => {
    if (!confirming) return;
    let active = true;
    api.get<AutopilotSettings>('/project-autopilot/settings').then(value => {
      if (active) setSettings(value);
    }).catch(() => { if (active) setSettings(undefined); });
    return () => { active = false; };
  }, [confirming]);
  const requestKey = useRef('');
  const sending = useRef(false);
  const processUrl = '/assets/project/' + projectId + '?tab=process';
  const active = ['WAITING_WORKER', 'QUEUED', 'RUNNING', 'RETRY_WAIT'].includes(analysis?.latest_run?.status ?? '');
  const consented = analysis?.enabled && !analysis.consent_required;

  async function start() {
    if (sending.current) return;
    sending.current = true;
    setBusy(true);
    setError('');
    try {
      requestKey.current ||= crypto.randomUUID();
      await api.post('/projects/' + projectId + '/ai-scan', {request_key: requestKey.current, acknowledge_source_transfer: !consented});
      requestKey.current = '';
      setConfirming(false);
      onQueued?.();
      navigate(processUrl);
    } catch (e) { setError(messageOf(e)); }
    finally { sending.current = false; setBusy(false); }
  }
  return <>
    <button type="button" className="btn btn-primary project-scan-button" disabled={busy} onClick={() => {
      if (active) navigate(processUrl);
      else if (consented) void start();
      else { setError(''); setConfirming(true); }
    }}>{active ? t('projects.scanProgress') : busy ? t('projects.submitting') : t('projects.aiScan')}</button>
    {error && !confirming && <span role="alert">{error}</span>}
    {confirming && <Modal title={t('projects.scanProjectTitle', {name})} onClose={() => {if (!busy) setConfirming(false);}} footer={<>
      <button className="btn btn-outline" disabled={busy} onClick={() => setConfirming(false)}>{t('projects.cancel')}</button>
      <button className="btn btn-primary" disabled={busy} onClick={() => void start()}>{busy ? t('projects.submitting') : t('projects.confirmStartScan')}</button>
    </>}>
      <dl className="scan-scope">
        <div><dt>{t('projects.repositoryAndBranch')}</dt><dd>{repository || name} / {branch || t('projects.defaultBranch')}</dd></div>
        <div><dt>{t('projects.scanScope')}</dt><dd>{t('projects.eligibleSourceCode')}</dd></div>
        <div><dt>{t('projects.modelRouting')}</dt><dd>{projectProviderName(settings) || t('projects.autoStudyModelConfig')}</dd></div>
        <div><dt>{t('projects.totalScanBudget')}</dt><dd>{settings?.budget ? [settings.budget.max_model_calls ? t('projects.scanBudgetCalls', {count: settings.budget.max_model_calls}) : t('projects.unlimitedModelCalls'), settings.budget.max_source_bytes ? t('projects.scanBudgetBytes', {count: settings.budget.max_source_bytes}) : t('projects.unlimitedSourceBytes')].join(' · ') : t('projects.savedScanBudget')}</dd></div>
      </dl>
      <p>{t('projects.scanConsent')}</p>
      <p className="hint">{t('projects.thisScanOnly')}</p>
      {error && <p role="alert">{error}</p>}
    </Modal>}
  </>;
}
