import { useEffect, useState } from 'react';
import { request } from './api';

type Entry = { id: number; text: string; revision: number };

export default function Memory() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState<number | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  async function refresh() {
    setError('');
    try { setEntries(await request<Entry[]>('/memories')); } catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); }, []);
  async function save() {
    setBusy(true); setError('');
    try {
      const value = await request<Entry>(editing === null ? '/memories' : '/memories/' + editing, {
        method: editing === null ? 'POST' : 'PUT', body: JSON.stringify({text:draft.trim(),source:'user_statement',confirmed:true}),
      });
      setEntries(old => [value, ...old.filter(entry => entry.id !== value.id)]);
      setDraft(''); setEditing(null);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function remove(id: number) {
    setBusy(true); setError('');
    try {
      await request('/memories/' + id, {method:'DELETE'});
      setEntries(old => old.filter(e => e.id !== id)); setRemoving(null);
      if (editing === id) { setEditing(null); setDraft(''); }
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <div className="stack"><header className="page-heading"><h1>项目记忆</h1>
    <p>写下你亲自做过的事。确认后，学习助手和面试官可以引用这些经历。</p></header>
    <form className="panel stack" onSubmit={e => {e.preventDefault(); void save();}}>
      <label htmlFor="experience">{editing === null ? '添加一段经历' : '修改这段经历'}</label>
      <textarea id="experience" value={draft} rows={4} maxLength={2400} required onChange={e => setDraft(e.target.value)} placeholder="例如：我负责一次校园社群拉新，设计了两种邀请文案，并比较它们的转化率。" />
      <div className="actions"><button className="primary" disabled={busy || !draft.trim()}>确认并保存经历</button>
        {editing !== null && <button type="button" onClick={() => {setEditing(null); setDraft('');}}>取消修改</button>}</div>
    </form>
    {error && <p role="alert" className="error">{error} <button onClick={refresh}>重试</button></p>}
    {loading ? <p role="status">正在读取经历…</p> : <section className="panel stack"><h2>已确认的经历</h2>
      {entries.length === 0 && <p className="muted">还没有保存的经历。</p>}
      {entries.map(entry => <article key={entry.id} className="memory-entry"><p>{entry.text}</p>
        <div className="actions"><button disabled={busy} onClick={() => {setDraft(entry.text); setEditing(entry.id); document.getElementById('experience')?.focus();}}>修改</button>
          {removing !== entry.id ? <button disabled={busy} onClick={() => setRemoving(entry.id)}>删除</button>
          : <><button className="danger" disabled={busy} onClick={() => remove(entry.id)}>确认删除这段经历</button><button onClick={() => setRemoving(null)}>取消</button></>}</div>
      </article>)}</section>}
  </div>;
}
