import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';

export default function StudyEntryButton({kind, id, bankKey}: {kind: 'question'|'exam_question'|'task'; id?: number; bankKey?: string}) {
  const navigate = useNavigate();
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const opening = useRef(false);
  async function open() {
    if (opening.current) return;
    opening.current = true;
    setBusy(true);
    setError('');
    try {
      const sourceId = id ?? (await api.get('/bank/'+encodeURIComponent(bankKey || ''))).id;
      const session = await api.post('/study/sessions',{source_type:kind,source_id:sourceId});
      navigate('/study/chat/'+session.id);
    } catch(e) { setError((e as Error).message); setBusy(false); opening.current = false; }
  }
  return <><button type="button" className="btn btn-outline btn-sm" disabled={busy} onClick={open}>{busy?'打开中…':'围绕这个问题学习'}</button>{error && <span role="alert">{error}</span>}</>;
}
