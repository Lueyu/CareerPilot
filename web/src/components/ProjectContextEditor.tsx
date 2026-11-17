import React, {useState} from 'react';
import {api} from '../api/client';
import {messageOf, PROFILE_FIELDS, ProjectProfile, ProjectRecord} from '../lib/questionWorkshop';

export default function ProjectContextEditor({project,onSaved,onCancel}: {project: ProjectRecord | null; onSaved:()=>Promise<void>; onCancel:()=>void}) {
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
    <h3>{project ? `补充真实经历 · ${project.name}` : '添加工作或个人项目'}</h3>
    <p className="hint">只写自己能够确认的内容。仓库扫描只能描述代码，不能证明你的个人贡献。未填写内容不会被视为已完成经历。</p>
    {!project && <label className="field"><span>项目名称</span><input className="inp" aria-label="项目名称" value={name} maxLength={120} onChange={e=>setName(e.target.value)}/></label>}
    <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(260px,1fr))',gap:14}}>{PROFILE_FIELDS.map(([key,label,placeholder]) => <label className="field" key={key}><span>{label}</span><textarea className="textarea" aria-label={label} rows={3} maxLength={5000} placeholder={placeholder} value={profile[key] || ''} onChange={e=>setProfile(old=>({...old,[key]:e.target.value}))}/></label>)}</div>
    {error && <p role="alert" className="demo-banner demo-banner-err">{error}</p>}
    <div style={{display:'flex',gap:10}}><button className="btn btn-primary" disabled={busy || !name.trim()} onClick={save}>{busy?'保存中…':'保存项目资料'}</button><button className="btn btn-outline" disabled={busy} onClick={onCancel}>取消</button></div>
  </section>;
}
