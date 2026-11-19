import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, getWorkspace } from '../api/client';
import './studyWorkspace.css';

export function draftKey(id: string | number) {
  return 'cp:study:' + encodeURIComponent(getWorkspace()) + ':' + id;
}
export function stored(key: string) {
  try { return localStorage.getItem(key) || ''; } catch { return ''; }
}
export function remember(key: string, value: string) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch { /* The workspace also works with browser storage disabled. */ }
}
export function submitKey(event: KeyboardEvent<HTMLTextAreaElement>, send: () => void) {
  if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
    event.preventDefault();
    if (!event.repeat) send();
  }
}

interface SessionRow { id: number; title: string; updated_at: string }
const sections = {
  chat: { title: '学习聊天', path: '/study/chat', icon: new URL('../assets/study/chat.png', import.meta.url).href },
  notes: { title: '我的笔记', path: '/study/notes', icon: new URL('../assets/study/notes.png', import.meta.url).href },
  review: { title: '复习与练习', path: '/study/notes?tab=review', icon: new URL('../assets/study/review.png', import.meta.url).href },
};
type Section = keyof typeof sections;
const narrowScreen = '(max-width: 1024px)';
const pageSize = 50;
const dayGroups = ['今天', '昨天', '近七天', '较早'];
function dayGroup(value: string) {
  const date = new Date(value);
  const midnight = (d: Date) => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  const days = (midnight(new Date()) - midnight(date)) / 86400000;
  return dayGroups[days <= 0 ? 0 : days === 1 ? 1 : days <= 7 ? 2 : 3];
}

export default function StudyShell({ id, section = 'chat', children }: {
  id?: string; section?: Section; children: ReactNode;
}) {
  const navigate = useNavigate();
  const [narrow, setNarrow] = useState(() => window.matchMedia(narrowScreen).matches);
  const [rail, setRail] = useState(() => stored('cp:study:sidebar') !== 'closed');
  const [drawer, setDrawer] = useState(false);
  const [term, setTerm] = useState('');
  const [list, setList] = useState({ query: '', page: 0, revision: 0 });
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [latest, setLatest] = useState<number>();
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const opener = useRef<HTMLButtonElement>(null);
  const closer = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLElement>(null);
  const main = useRef<HTMLElement>(null);
  const open = narrow ? drawer : rail;
  const current = sections[section];
  const resumeId = id || latest;
  const chatPath = resumeId ? '/study/chat/' + resumeId : sections.chat.path;
  const activeTitle = id ? rows.find(row => String(row.id) === id)?.title : undefined;

  useEffect(() => {
    const mq = window.matchMedia(narrowScreen);
    function resize() { setNarrow(mq.matches); setDrawer(false); }
    mq.addEventListener('change', resize);
    return () => mq.removeEventListener('change', resize);
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const query = term.trim();
      setList(old => old.query === query ? old : { ...old, query, page: 0 });
    }, 180);
    return () => window.clearTimeout(timer);
  }, [term]);
  useEffect(() => {
    const refresh = () => setList(old => ({ ...old, page: 0, revision: old.revision + 1 }));
    window.addEventListener('study-history-changed', refresh);
    return () => window.removeEventListener('study-history-changed', refresh);
  }, []);
  useEffect(() => {
    let currentRequest = true;
    setLoading(true);
    setError('');
    if (!list.page) setMore(false);
    const params = new URLSearchParams({ limit: String(pageSize), offset: String(list.page * pageSize), q: list.query });
    api.get<{ items: SessionRow[] }>('/study/sessions?' + params)
      .then(({ items }) => {
        if (!currentRequest) return;
        setRows(old => list.page === 0 ? items : [...new Map([...old, ...items].map(row => [row.id, row])).values()]);
        setMore(items.length === pageSize);
        if (!list.query && !list.page) setLatest(items[0]?.id);
      })
      .catch((err: Error) => { if (currentRequest) setError(err.message); })
      .finally(() => { if (currentRequest) setLoading(false); });
    return () => { currentRequest = false; };
  }, [list, id]);

  useEffect(() => {
    if (!drawer || !narrow) return;
    closer.current?.focus();
    const content = main.current;
    content?.setAttribute('inert', '');
    function keydown(event: globalThis.KeyboardEvent) {
      if (event.key === 'Escape') {
        setDrawer(false);
        opener.current?.focus();
      }
      if (event.key !== 'Tab') return;
      const links = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input, summary') ?? [])
        .filter(el => el.getClientRects().length);
      if (event.shiftKey && document.activeElement === links[0]) {
        event.preventDefault(); links[links.length - 1]?.focus();
      } else if (!event.shiftKey && document.activeElement === links[links.length - 1]) {
        event.preventDefault(); links[0]?.focus();
      }
    }
    window.addEventListener('keydown', keydown);
    return () => { content?.removeAttribute('inert'); window.removeEventListener('keydown', keydown); };
  }, [drawer, narrow]);

  function toggleRail(next: boolean) {
    if (narrow) setDrawer(next);
    else { setRail(next); remember('cp:study:sidebar', next ? 'open' : 'closed'); }
    if (!next) requestAnimationFrame(() => opener.current?.focus());
  }
  function closeAfterNavigation() { if (narrow) setDrawer(false); }
  async function manage(row: SessionRow, action: 'rename' | 'remove') {
    try {
      if (action === 'rename') {
        const title = window.prompt('新会话标题', row.title)?.trim();
        if (!title || title === row.title) return;
        await api.patch('/study/sessions/' + row.id, { title: title.slice(0, 160) });
      } else {
        if (!window.confirm('从历史中移除「' + row.title + '」？')) return;
        await api.del('/study/sessions/' + row.id);
        if (String(row.id) === id) navigate('/study/chat');
      }
      window.dispatchEvent(new Event('study-history-changed'));
    } catch (err) { setError((err as Error).message); }
  }
  const visibleRows = rows.filter(row => row.title.toLocaleLowerCase().includes(term.trim().toLocaleLowerCase()));
  return <div className={'coach-shell study-workspace' + (open ? ' sidebar-open' : '')}>
    {open && <>
      {narrow && <button type="button" className="workspace-scrim" tabIndex={-1} aria-label="关闭会话导航" onClick={() => toggleRail(false)} />}
      <aside ref={panel} className="workspace-rail" id="coach-history" aria-label="学习导航" role={narrow ? 'dialog' : undefined} aria-modal={narrow || undefined}>
        <div className="workspace-brand-row">
          <strong>学习工作区</strong>
          <button ref={closer} type="button" className="workspace-icon" aria-label="收起学习导航" title="收起学习导航" onClick={() => toggleRail(false)}>‹</button>
        </div>
        <Link to="/study/chat" className="workspace-new" onClick={closeAfterNavigation}>新建学习会话</Link>
        <nav className="workspace-sections" aria-label="学习工作区">
          {(Object.keys(sections) as Section[]).map(key => <Link key={key} to={key === 'chat' ? chatPath : sections[key].path} aria-current={key === section ? 'page' : undefined} onClick={closeAfterNavigation}>
            <img src={sections[key].icon} alt="" />{sections[key].title}
          </Link>)}
        </nav>
        <div className="workspace-history-label"><span>最近会话</span></div>
        <input className="workspace-search" type="search" aria-label="搜索会话" placeholder="搜索会话" value={term} onChange={e => setTerm(e.target.value)} />
        <nav className="workspace-history" aria-label="历史会话">
          {dayGroups.map(group => {
            const items = visibleRows.filter(row => dayGroup(row.updated_at) === group);
            return items.length > 0 && <section key={group}><h2>{group}</h2>{items.map(row =>
              <div key={row.id} className={'workspace-history-item' + (String(row.id) === id ? ' selected' : '')}>
                <Link title={row.title} aria-current={String(row.id) === id ? 'page' : undefined} to={'/study/chat/' + row.id} onClick={closeAfterNavigation}>{row.title}</Link>
                <details><summary aria-label={'管理会话：' + row.title}>⋯</summary><div>
                  <button onClick={e => { e.currentTarget.closest('details')?.removeAttribute('open'); void manage(row, 'rename'); }}>重命名</button>
                  <button onClick={e => { e.currentTarget.closest('details')?.removeAttribute('open'); void manage(row, 'remove'); }}>删除会话</button>
                </div></details>
              </div>)}</section>;
          })}
          {loading && <small role="status">正在加载会话…</small>}
          {error && <p role="alert">{error} <button onClick={() => setList(s => ({ ...s, revision: s.revision + 1 }))}>重试</button></p>}
          {!loading && !error && !visibleRows.length && <p>{term ? '没有匹配的会话' : '还没有学习会话'}</p>}
          {more && !loading && <button className="btn btn-ghost" onClick={() => setList(s => ({ ...s, page: s.page + 1 }))}>加载更多会话</button>}
        </nav>
      </aside>
    </>}
    <div className="workspace-content">
      {(!open || narrow) && <header className="workspace-bar">
        {(!open || narrow) && <button ref={opener} className="workspace-menu" aria-label="打开会话导航" aria-controls="coach-history" aria-expanded={open} onClick={() => toggleRail(true)}>会话</button>}
        <div className="workspace-crumb"><Link to={current.path}>{current.title}</Link>{activeTitle && <><span aria-hidden="true">/</span><strong>{activeTitle}</strong></>}</div>
        <span className="workspace-desktop-name">学习工作台</span>
        <Link className="workspace-mobile-link" to={section === 'chat' ? '/study/notes' : chatPath}>{section === 'chat' ? '笔记' : '聊天'}</Link>
      </header>}
      <main ref={main} className="coach-main">{children}</main>
    </div>
  </div>;
}
