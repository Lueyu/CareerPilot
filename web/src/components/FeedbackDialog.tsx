import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { ApiError } from '../api/client';
import { SeedApiError } from '../seed/api';
import {
  createFeedbackId, feedbackCompareUrl, feedbackForkUrl, feedbackResult, feedbackWasSent, type SendFeedback,
} from '../api/feedback';
import './feedbackDialog.css';

const tabs = ['提意见', '发起 PR'] as const;
type FeedbackState =
  | { kind: 'idle' | 'sending' | 'sent' }
  | { kind: 'error'; message: string };

export default function FeedbackDialog({ send }: { send: SendFeedback }) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const tabButtons = useRef<(HTMLButtonElement | null)[]>([]);
  const request = useRef<AbortController | null>(null);
  const submissions = useRef(new Map<string, string>());
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState(0);
  const [draft, setDraft] = useState('');
  const [state, setState] = useState<FeedbackState>({ kind: 'idle' });
  const [owner, setOwner] = useState('');
  const [branch, setBranch] = useState('');

  const clearPrivateState = useCallback(() => {
    request.current?.abort();
    request.current = null;
    submissions.current.clear();
    setDraft('');
    setOwner('');
    setBranch('');
    setState({ kind: 'idle' });
    dialog.current?.close();
  }, []);

  useEffect(() => {
    function clearOnRestore(event: PageTransitionEvent) {
      if (event.persisted) clearPrivateState();
    }
    // A workspace switch navigates to a new document, which can leave this one in the back/forward cache.
    if (typeof window !== 'undefined' && window.addEventListener) window.addEventListener('pageshow', clearOnRestore);
    return () => {
      if (typeof window !== 'undefined' && window.removeEventListener) window.removeEventListener('pageshow', clearOnRestore);
      request.current?.abort();
    };
  }, [clearPrivateState]);

  const message = draft.trim();
  const length = Array.from(message).length;
  const compareUrl = feedbackCompareUrl(owner.trim(), branch);
  const busy = state.kind === 'sending';

  function moveTab(event: KeyboardEvent<HTMLButtonElement>) {
    const { key } = event;
    if (key !== 'ArrowLeft' && key !== 'ArrowRight' && key !== 'Home' && key !== 'End') return;
    const next = key === 'Home' ? 0 : key === 'End' ? 1 : 1 - tab;
    event.preventDefault();
    setTab(next);
    tabButtons.current[next]?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (request.current || length < 1 || length > 2000) return;

    const controller = new AbortController();
    request.current = controller;
    setState({ kind: 'sending' });
    try {
      const submissionId = submissions.current.get(message) ?? createFeedbackId();
      submissions.current.set(message, submissionId);
      const result = await send({ submission_id: submissionId, message }, controller.signal);
      if (controller.signal.aborted) return;
      if (!feedbackWasSent(result)) {
        setState({ kind: 'error', message: feedbackResult(0) });
        return;
      }
      submissions.current.delete(message);
      setDraft('');
      setState({ kind: 'sent' });
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof SeedApiError && error.status === 409 && error.code === 'workspace_changed') {
        clearPrivateState();
        return;
      }
      const status = error instanceof ApiError || error instanceof SeedApiError ? error.status : 0;
      setState({ kind: 'error', message: feedbackResult(status) });
    } finally {
      if (request.current === controller) request.current = null;
    }
  }

  return <>
    <button ref={trigger} className="btn feedback-entry" type="button" aria-haspopup="dialog"
      aria-controls={id} aria-expanded={open} onClick={() => {
        dialog.current?.showModal();
        setOpen(true);
        tabButtons.current[0]?.focus();
      }}>提意见</button>
    <dialog ref={dialog} className="feedback-dialog" id={id} aria-labelledby={id + '-title'}
      onClose={() => {
        setOpen(false);
        setTab(0);
        trigger.current?.focus();
      }}>
      <header className="feedback-dialog-header">
        <h2 id={id + '-title'}>意见与贡献</h2>
        <button className="btn feedback-close" type="button" onClick={() => dialog.current?.close()}>关闭</button>
      </header>
      <div className="feedback-tabs" role="tablist" aria-label="意见与贡献">
        {tabs.map((label, index) => <button key={label} type="button" role="tab"
          ref={element => { tabButtons.current[index] = element; }}
          id={id + '-tab-' + index} aria-controls={id + '-panel-' + index}
          aria-selected={tab === index} tabIndex={tab === index ? 0 : -1}
          onKeyDown={moveTab} onClick={() => setTab(index)}>{label}</button>)}
      </div>
      <div role="tabpanel" id={id + '-panel-0'} aria-labelledby={id + '-tab-0'} hidden={tab !== 0}>
        <form className="feedback-form" onSubmit={submit}>
          <label htmlFor={id + '-message'}>你的意见</label>
          <textarea id={id + '-message'} value={draft} readOnly={busy} autoComplete="off"
            aria-describedby={id + '-privacy'} aria-invalid={length > 2000}
            placeholder="哪里不顺手？你希望怎样改进？"
            onChange={event => {
              setDraft(event.target.value);
              setState({ kind: 'idle' });
            }} />
          {length > 2000 && <p role="alert">反馈最多 2000 字，请缩短后发送。</p>}
          <p id={id + '-privacy'}>反馈文本会发送至内容审核服务，通过后以邮件发给维护者。请勿填写隐私或账号信息。</p>
          <button className="btn btn-primary" type="submit" disabled={busy || length < 1 || length > 2000}>
            {busy ? '正在发送…' : '发送反馈'}
          </button>
          {state.kind === 'sent' && <p role="status">反馈已交给邮件服务器。</p>}
          {state.kind === 'error' && <p role="alert">{state.message}</p>}
        </form>
      </div>
      <div role="tabpanel" id={id + '-panel-1'} aria-labelledby={id + '-tab-1'} hidden={tab !== 1}>
        <div className="feedback-form">
          <label htmlFor={id + '-owner'}>GitHub 用户名</label>
          <input id={id + '-owner'} value={owner} onChange={event => setOwner(event.target.value)}
            placeholder="例如 linzhou" autoComplete="off" autoCapitalize="none" spellCheck={false} />
          <label htmlFor={id + '-branch'}>包含改动的分支</label>
          <input id={id + '-branch'} value={branch} onChange={event => setBranch(event.target.value)}
            placeholder="例如 improve-search" autoComplete="off" autoCapitalize="none" spellCheck={false} />
          <p>在 GitHub 核对差异，向 dev 提交公开 PR。还没有分支？先 <a href={feedbackForkUrl}
            target="_blank" rel="noreferrer">Fork 仓库</a>，再创建分支并推送改动。</p>
          {owner.trim() && branch && !compareUrl && <p role="alert">请输入有效的 GitHub 用户名和分支名。</p>}
          {compareUrl ? <a className="btn btn-primary" href={compareUrl} target="_blank" rel="noreferrer">前往 GitHub</a>
            : <button className="btn btn-primary" type="button" disabled>前往 GitHub</button>}
        </div>
      </div>
    </dialog>
  </>;
}
