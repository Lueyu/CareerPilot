import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useT } from '../i18n';
import { announceSessionChange, messageFor, renderMessage, requestAuth, type AccountInfo, type MessageKey, type UiMessage } from './api';
import { track } from '../telemetry';

type Mode = 'login' | 'register' | 'recover';
type RecoveryInfo = AccountInfo & { recovery_code: string };

const modes: ReadonlyArray<{ mode: Mode; labelKey: MessageKey }> = [
  { mode: 'login', labelKey: 'registered.login' },
  { mode: 'register', labelKey: 'registered.register' },
  { mode: 'recover', labelKey: 'registered.recoverAccount' },
];

const submitLabelKeys: Record<Mode, MessageKey> = {
  login: 'registered.login',
  register: 'registered.createAccount',
  recover: 'registered.resetPassword',
};

function RecoveryCode({ code, onContinue }: { code: string; onContinue: () => void }) {
  const t = useT();
  const titleId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const [copyStatus, setCopyStatus] = useState<MessageKey>();

  useEffect(() => {
    heading.current?.focus();
  }, []);

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(code);
      setCopyStatus('registered.recoveryCopied');
    } catch {
      setCopyStatus('registered.recoveryCopyFailed');
    }
  }

  return (
    <section className="card account-card" aria-labelledby={titleId}>
      <h2 ref={heading} id={titleId} tabIndex={-1}>
        {t('registered.saveRecoveryCode')}
      </h2>
      <p>{t('registered.recoveryCodeInstructions')}</p>
      <code style={{ overflowWrap: 'anywhere' }}>{code}</code>
      <div className="account-actions">
        <button className="btn btn-outline" type="button" onClick={copyCode}>
          {t('registered.copyRecoveryCode')}
        </button>
        <button className="btn btn-primary" type="button" onClick={onContinue}>
          {t('registered.confirmRecoveryCodeSaved')}
        </button>
      </div>
      <span role="status">{copyStatus && t(copyStatus)}</span>
    </section>
  );
}

export default function Account({ onStart, onFinish }: { onStart: () => void; onFinish: () => void }) {
  const t = useT();
  const [mode, setMode] = useState<Mode>('login');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<UiMessage | null>(null);
  const [recoveryCode, setRecoveryCode] = useState<string | null>(null);
  const titleId = useId();
  const usernameHint = useId();
  const passwordHint = useId();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;

    const fields = new FormData(event.currentTarget);
    const credentials = {
      username: fields.get('username'),
      password: fields.get('password'),
    };
    const body = mode === 'recover'
      ? { ...credentials, recovery_code: fields.get('recovery_code') }
      : credentials;
    const options = { method: 'POST', body: JSON.stringify(body) };
    onStart();
    setPending(true);
    setError(null);

    let awaitingConfirmation = false;
    try {
      if (mode === 'login') {
        await requestAuth<AccountInfo>('/auth/login', options);
        track('auth.login');
      } else {
        const result = await requestAuth<RecoveryInfo>(`/auth/${mode}`, options);
        track(mode === 'register' ? 'auth.register' : 'auth.recover');
        awaitingConfirmation = true;
        setRecoveryCode(result.recovery_code);
      }
      announceSessionChange();
    } catch (cause) {
      setError(messageFor(cause, 'registered.accountUnavailable'));
    } finally {
      setPending(false);
      if (!awaitingConfirmation) onFinish();
    }
  }

  if (recoveryCode !== null) {
    return (
      <RecoveryCode
        code={recoveryCode}
        onContinue={() => {
          setRecoveryCode(null);
          onFinish();
        }}
      />
    );
  }

  return (
    <section className="card account-card" aria-labelledby={titleId}>
      <h2 id={titleId}>{mode === 'register' ? t('registered.createCareerPilotAccount') : mode === 'recover' ? t('registered.recoverCareerPilotAccount') : t('registered.loginToCareerPilot')}</h2>
      <div className="account-modes" role="group" aria-label={t('registered.accountActions')}>
        {modes.map(({ mode: candidate, labelKey }) => (
          <button
            key={candidate}
            type="button"
            className={mode === candidate ? 'btn btn-primary' : 'btn btn-outline'}
            aria-pressed={mode === candidate}
            disabled={pending}
            onClick={() => {
              setMode(candidate);
              setError(null);
            }}
          >
            {t(labelKey)}
          </button>
        ))}
      </div>
      <form key={mode} className="account-form" onSubmit={submit} aria-busy={pending}>
        <label className="account-form">
          {t('registered.username')}
          <input
            name="username"
            placeholder={mode === 'register' ? t('registered.usernamePlaceholder') : undefined}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            pattern="[A-Za-z0-9_]{3,32}"
            maxLength={32}
            aria-describedby={mode === 'register' ? usernameHint : undefined}
            disabled={pending}
            required
          />
        </label>
        {mode === 'register' && (
          <small id={usernameHint} className="account-input-hint">{t('registered.usernameHint')}</small>
        )}
        {mode === 'recover' && (
          <label className="account-form">
            {t('registered.recoveryCode')}
            <input
              name="recovery_code"
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              disabled={pending}
              required
            />
          </label>
        )}
        <label className="account-form">
          {mode === 'recover' ? t('registered.newPassword') : t('registered.password')}
          <input
            type="password"
            name="password"
            placeholder={mode !== 'login' ? t('registered.passwordPlaceholder') : undefined}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            minLength={mode === 'login' ? undefined : 12}
            maxLength={128}
            aria-describedby={mode === 'login' ? undefined : passwordHint}
            disabled={pending}
            required
          />
        </label>
        {mode !== 'login' && (
          <small id={passwordHint} className="account-input-hint">{t('registered.passwordHint')}</small>
        )}
        {error && <p className="error" role="alert">{renderMessage(error, t)}</p>}
        <div className="account-actions">
          <button type="submit" className="btn btn-primary" disabled={pending}>
            {pending ? t('registered.accountSubmitting') : t(submitLabelKeys[mode])}
          </button>
        </div>
      </form>
    </section>
  );
}
