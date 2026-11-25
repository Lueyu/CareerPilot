import { useT } from '../i18n';
import React, { useEffect, useState } from 'react';
import { api, RetestHistoricalMapping, RetestMappingCriterion, RetestMappings } from '../api/client';
import { messageOf } from '../lib/questionWorkshop';

export default function RetestMappingReview({ questionId }: { questionId: number }) {
  const t = useT();
  const path = `/retest-mappings/${questionId}`;
  const [data, setData] = useState<RetestMappings | null>(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    api.get<RetestMappings>(path).then(
      result => { if (active) setData(result); },
      failure => { if (active) setError(messageOf(failure)); },
    );
    return () => { active = false; };
  }, [path, reload]);

  if (error) return <>
    <p role="alert" className="demo-banner demo-banner-err">{error}</p>
    <button className="btn btn-outline btn-sm" onClick={() => { setError(''); setReload(count => count + 1); }}>{t('bank.reloadCriteria')}</button>
  </>;
  if (!data) return <p role="status">{t('bank.loadingCriteria')}</p>;

  const historical = data.historical_mappings ?? [];

  return <section aria-label={t('bank.retestCriteriaMapping')}>
    <p className="hint">{t('bank.rubricMappingHelp', { version: data.rubric_version || t('bank.unrecorded') })}</p>
    {!data.reviewable && <p className="hint">{t('bank.mappingReviewUnavailable')}</p>}
    {data.criteria.length === 0 && <p className="hint">{t('bank.noCriteria')}</p>}
    {data.criteria.map(criterion => (
      <CriterionReview key={`${data.rubric_version}:${criterion.criterion_id}`} path={path}
        rubricVersion={data.rubric_version} reviewable={data.reviewable} initial={criterion} />
    ))}
    {historical.length > 0 && <section aria-label={t('bank.historicalMappings')}>
      <h4>{t('bank.historicalMappings')}</h4>
      <p className="hint">{t('bank.historicalMappingHelp')}</p>
      {historical.map(mapping => (
        <HistoricalReview key={JSON.stringify([mapping.rubric_version, mapping.criterion_id])} path={path} mapping={mapping} />
      ))}
    </section>}
  </section>;
}

function revokeMapping(path: string, criterionId: string, rubricVersion: string) {
  const query = new URLSearchParams({ rubric_version: rubricVersion });
  return api.del<void>(`${path}/${encodeURIComponent(criterionId)}?${query}`);
}

function HistoricalReview({ path, mapping }: { path: string; mapping: RetestHistoricalMapping }) {
  const t = useT();
  const [status, setStatus] = useState<'ready' | 'pending' | 'removed'>('ready');
  const [error, setError] = useState('');

  async function remove() {
    setStatus('pending');
    setError('');
    try {
      await revokeMapping(path, mapping.criterion_id, mapping.rubric_version);
      setStatus('removed');
    } catch (failure) {
      setError(messageOf(failure));
      setStatus('ready');
    }
  }

  return <div className="card" style={{ marginBottom: 12 }}>
    <p>{t('bank.historicalCriterion', { criterion: mapping.criterion_id, version: mapping.rubric_version })}</p>
    <p className="hint">{t('bank.historicalBehavior', { behavior: mapping.behavior_key, version: mapping.behavior_version })}</p>
    {status === 'removed' ? <p role="status">{t('bank.historicalMappingRevoked')}</p> : (
      <button type="button" className="btn btn-outline btn-sm" disabled={status === 'pending'} onClick={remove}>{t('bank.revokeHistoricalMapping')}</button>
    )}
    {error && <p role="alert" className="demo-banner demo-banner-err">{error}</p>}
  </div>;
}

function CriterionReview({ path, rubricVersion, reviewable, initial }: {
  path: string; rubricVersion: string; reviewable: boolean; initial: RetestMappingCriterion;
}) {
  const t = useT();
  const [criterion, setCriterion] = useState(initial);
  const [key, setKey] = useState(initial.behavior_key ?? '');
  const [version, setVersion] = useState(String(initial.behavior_version ?? 1));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState<'bank.mappingApproved' | 'bank.mappingRevoked' | ''>('');
  const behaviorVersion = Number(version);
  const valid = /^[a-z0-9][a-z0-9._-]{0,127}$/.test(key) && Number.isInteger(behaviorVersion)
    && behaviorVersion >= 1 && behaviorVersion <= 2147483647;
  const criterionPath = `${path}/${encodeURIComponent(criterion.criterion_id)}`;

  async function approve(event: React.FormEvent) {
    event.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const updated = await api.put<RetestMappingCriterion>(criterionPath, {
        rubric_version: rubricVersion, behavior_key: key, behavior_version: behaviorVersion, confirm: true,
      });
      setCriterion(updated);
      setNotice('bank.mappingApproved');
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await revokeMapping(path, criterion.criterion_id, rubricVersion);
      setCriterion({ ...criterion, behavior_key: null, behavior_version: null, reviewed_at: null });
      setKey('');
      setVersion('1');
      setNotice('bank.mappingRevoked');
    } catch (failure) {
      setError(messageOf(failure));
    } finally {
      setBusy(false);
    }
  }

  return <div className="card" style={{ marginBottom: 12 }}>
    <h4>{criterion.title || criterion.criterion_id}</h4>
    <p className="hint">{criterion.behavior_key ? t('bank.reviewedBehavior', { behavior: criterion.behavior_key, version: String(criterion.behavior_version) }) : t('bank.mappingPending')}</p>
    {reviewable && <form onSubmit={approve}>
      <label className="field">
        <span>{t('bank.behaviorKey')}</span>
        <input className="inp" type="text" value={key} maxLength={128} placeholder="rag.retrieval-metrics" disabled={busy} onChange={event => setKey(event.target.value)} />
      </label>
      <label className="field">
        <span>{t('bank.behaviorVersion')}</span>
        <input className="inp" type="number" min={1} max={2147483647} step={1} value={version} disabled={busy} onChange={event => setVersion(event.target.value)} />
      </label>
      <button className="btn btn-primary btn-sm" type="submit" disabled={busy || !valid}>{t('bank.confirmMapping')}</button>
      {criterion.behavior_key && <button className="btn btn-outline btn-sm" type="button" disabled={busy} onClick={remove}>{t('bank.revokeMapping')}</button>}
    </form>}
    {notice && <p role="status">{t(notice)}</p>}
    {error && <p role="alert" className="demo-banner demo-banner-err">{error}</p>}
  </div>;
}
