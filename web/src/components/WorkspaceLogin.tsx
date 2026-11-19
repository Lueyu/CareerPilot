import { useState, type ReactNode } from 'react';
import { api, workspaceLogin } from '../api/client';

export default function WorkspaceLogin({children}:{children:ReactNode}) {
  const [ready,setReady]=useState(()=>!workspaceLogin || !!sessionStorage.getItem('cp-login-token'));
  const [slug,setSlug]=useState(''); const [token,setToken]=useState('');
  const [error,setError]=useState(''); const [busy,setBusy]=useState(false);
  function clear(){sessionStorage.removeItem('cp-login-workspace');sessionStorage.removeItem('cp-login-token')}
  if(ready)return <>{workspaceLogin&&<button className="btn btn-outline" onClick={()=>{clear();setReady(false)}}>退出当前工作空间</button>}{children}</>;
  return <form className="card" style={{maxWidth:420,margin:'12vh auto',display:'grid',gap:12}} onSubmit={async e=>{
    e.preventDefault();setBusy(true);setError('');
    sessionStorage.setItem('cp-login-workspace',slug.trim());sessionStorage.setItem('cp-login-token',token.trim());
    try{await api.get('/settings/overview');setToken('');setReady(true)}catch(ex){clear();setError((ex as Error).message)}finally{setBusy(false)}
  }}><h2>进入 CareerPilot 工作空间</h2><label>工作空间 <input required value={slug} onChange={e=>setSlug(e.target.value)}/></label>
  <label>个人访问令牌 <input type="password" required value={token} onChange={e=>setToken(e.target.value)}/></label>
  {error&&<p role="alert">{error}</p>}<button disabled={busy} className="btn btn-primary">进入工作空间</button></form>
}
