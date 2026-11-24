import {useT} from '../i18n';
import React, {useState} from 'react';
import {PROJECT_PROFILE_COPY} from '../lib/projectIntelligence';
import {api} from '../api/client';
import {messageOf, PROFILE_FIELDS, ProjectProfile, ProjectRecord} from '../lib/questionWorkshop';

export default function ProjectContextEditor({project,onSaved,onCancel}: {project: ProjectRecord | null; onSaved:()=>Promise<void>; onCancel:()=>void}) {
  const t = useT();
  const [name,setName] = useState(project?.name || '');
  const [profile,setProfile] = useState<ProjectProfile>(project?.profile || project?.meta?.profile || {});
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const save = async () => {
    setBusy(true);setError('');
    try {
      if (project) await api.patch(`/projects/${project.id}/profile`,{profile});
      else await api.post('/projects',{name:name.trim(),kind:'manual',profile});
      await onSaved();
    } catch(e) {setError(messageOf(e));} finally {setBusy(false);}
  };
  return <section className="card" style={{padding:20,marginBottom:20}}>
    <h3>{project ? t('projects.editPersonalProject', {name: project.name}) : t('projects.addPersonalProject')}</h3>
    <p className="hint">{t('projects.profileGuidance')}</p>
    {!project && <label className="field"><span>{t('projects.projectName')}</span><input className="inp" aria-label={t('projects.projectName')} value={name} maxLength={120} onChange={e=>setName(e.target.value)}/></label>}
    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(260px,1fr))',gap:14}}>{PROFILE_FIELDS.map(([key]) => {
      const [labelKey, placeholderKey] = PROJECT_PROFILE_COPY[key];
      const label = t(labelKey);
      return <label className="field" key={key}><span>{label}</span><textarea className="textarea" aria-label={label} rows={3} maxLength={5000} placeholder={t(placeholderKey)} value={profile[key] || ''} onChange={e=>setProfile(old=>({...old,[key]:e.target.value}))}/></label>;
    })}</div>
    {error && <p role="alert" className="demo-banner demo-banner-err">{error}</p>}
    <div style={{display:'flex',gap:10}}><button className="btn btn-primary" disabled={busy || !name.trim()} onClick={save}>{busy?t('projects.saving'):t('projects.saveProjectProfile')}</button><button className="btn btn-outline" disabled={busy} onClick={onCancel}>{t('projects.cancel')}</button></div>
  </section>;
}
