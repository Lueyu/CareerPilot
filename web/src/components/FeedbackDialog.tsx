import { useT } from '../i18n';
import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { ApiError } from '../api/client';
import { SeedApiError } from '../seed/api';
import {
  createFeedbackId, feedbackCompareUrl, feedbackForkUrl, feedbackResult, feedbackWasSent, type SendFeedback,
} from '../api/feedback';
import './feedbackDialog.css';

const tabs = ['shell.feedback', 'shell.openPr'] as const;
type FeedbackState =
  | { kind: 'idle' | 'sending' | 'sent' }
  | { kind: 'error'; status: number };

export default function FeedbackDialog({ send }: { send: SendFeedback }) {
  const t = useT();
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
  const [forkBefore, forkAfter] = t('shell.feedbackPrInstructions', { fork: '{fork}' }).split('{fork}');

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
        setState({ kind: 'error', status: 0 });
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
      setState({ kind: 'error', status });
    } finally {
      if (request.current === controller) request.current = null;
    }
  }

  return <>
    <button ref={trigger} className="btn feedback-entry" type="button" aria-haspopup="dialog"
      aria-label={t('shell.feedback')}
      aria-controls={id} aria-expanded={open} onClick={() => {
        dialog.current?.showModal();
        setOpen(true);
        tabButtons.current[0]?.focus();
      }}>{t('shell.feedback')}<span className="feedback-entry-icon" aria-hidden="true">✉</span></button>
    <dialog ref={dialog} className="feedback-dialog" id={id} aria-labelledby={id + '-title'}
      onClose={() => {
        setOpen(false);
        setTab(0);
        trigger.current?.focus();
      }}>
      <header className="feedback-dialog-header">
        <h2 id={id + '-title'}>{t('shell.feedbackContribute')}</h2>
        <button className="btn feedback-close" type="button" onClick={() => dialog.current?.close()}>{t('shell.close')}</button>
      </header>
      <div className="feedback-tabs" role="tablist" aria-label={t('shell.feedbackContribute')}>
        {tabs.map((labelKey, index) => <button key={labelKey} type="button" role="tab"
          ref={element => { tabButtons.current[index] = element; }}
          id={id + '-tab-' + index} aria-controls={id + '-panel-' + index}
          aria-selected={tab === index} tabIndex={tab === index ? 0 : -1}
          onKeyDown={moveTab} onClick={() => setTab(index)}>{t(labelKey)}</button>)}
      </div>
      <div role="tabpanel" id={id + '-panel-0'} aria-labelledby={id + '-tab-0'} hidden={tab !== 0}>
        <form className="feedback-form" onSubmit={submit}>
          <label htmlFor={id + '-message'}>{t('shell.yourFeedback')}</label>
          <textarea id={id + '-message'} value={draft} readOnly={busy} autoComplete="off"
            aria-describedby={id + '-privacy'} aria-invalid={length > 2000}
            placeholder={t('shell.feedbackPrompt')}
            onChange={event => {
              setDraft(event.target.value);
              setState({ kind: 'idle' });
            }} />
          {length > 2000 && <p role="alert">{t('shell.feedbackTooLong')}</p>}
          <p id={id + '-privacy'}>{t('shell.feedbackPrivacy')}</p>
          <button className="btn btn-primary" type="submit" disabled={busy || length < 1 || length > 2000}>
            {busy ? t('shell.feedbackSending') : t('shell.sendFeedback')}
          </button>
          {state.kind === 'sent' && <p role="status">{t('shell.feedbackSent')}</p>}
          {state.kind === 'error' && <p role="alert">{feedbackResult(state.status, t)}</p>}
        </form>
      </div>
      <div role="tabpanel" id={id + '-panel-1'} aria-labelledby={id + '-tab-1'} hidden={tab !== 1}>
        <div className="feedback-form">
          <label htmlFor={id + '-owner'}>{t('shell.githubUsername')}</label>
          <input id={id + '-owner'} value={owner} onChange={event => setOwner(event.target.value)}
            placeholder={t('shell.githubUsernameExample')} autoComplete="off" autoCapitalize="none" spellCheck={false} />
          <label htmlFor={id + '-branch'}>{t('shell.githubBranch')}</label>
          <input id={id + '-branch'} value={branch} onChange={event => setBranch(event.target.value)}
            placeholder={t('shell.githubBranchExample')} autoComplete="off" autoCapitalize="none" spellCheck={false} />
          <p>{forkBefore}<a href={feedbackForkUrl}
            target="_blank" rel="noreferrer">{t('shell.forkRepository')}</a>{forkAfter}</p>
          {owner.trim() && branch && !compareUrl && <p role="alert">{t('shell.githubInvalidRef')}</p>}
          {compareUrl ? <a className="btn btn-primary" href={compareUrl} target="_blank" rel="noreferrer">{t('shell.goGithub')}</a>
            : <button className="btn btn-primary" type="button" disabled>{t('shell.goGithub')}</button>}
        </div>
      </div>
    </dialog>
  </>;
}
