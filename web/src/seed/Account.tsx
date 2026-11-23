import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { request } from './api';

type Mode = 'login' | 'register' | 'recover';
type AccountInfo = { username: string; workspace_id: number };
type RecoveryInfo = AccountInfo & { recovery_code: string };

const modes: ReadonlyArray<{ mode: Mode; label: string }> = [
  { mode: 'login', label: '登录' },
  { mode: 'register', label: '注册' },
  { mode: 'recover', label: '找回账号' },
];

const submitLabels: Record<Mode, string> = {
  login: '登录',
  register: '创建账号',
  recover: '重置密码',
};

function RecoveryCode({ code, onContinue }: { code: string; onContinue: () => void }) {
  const titleId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const [copyStatus, setCopyStatus] = useState('');

  useEffect(() => {
    heading.current?.focus();
  }, []);

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(code);
      setCopyStatus('已复制。');
    } catch {
      setCopyStatus('复制失败，请手动选中并保存恢复码。');
    }
  }

  return (
    <section className="panel stack" aria-labelledby={titleId}>
      <h2 ref={heading} id={titleId} tabIndex={-1}>
        保存你的恢复码
      </h2>
      <p>恢复码只显示这一次。忘记密码时，可凭用户名和恢复码重置密码。每个恢复码只能用一次。</p>
      <code style={{ overflowWrap: 'anywhere' }}>{code}</code>
      <div className="actions">
        <button className="secondary" type="button" onClick={copyCode}>
          复制恢复码
        </button>
        <button className="primary" type="button" onClick={onContinue}>
          我已保存，继续
        </button>
      </div>
      <span role="status">{copyStatus}</span>
    </section>
  );
}

export default function Account({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [mode, setMode] = useState<Mode>('login');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
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
    setPending(true);
    setError('');

    try {
      if (mode === 'login') {
        await request<AccountInfo>('/auth/login', options);
        onAuthenticated();
      } else {
        const result = await request<RecoveryInfo>(`/auth/${mode}`, options);
        setRecoveryCode(result.recovery_code);
      }
    } catch (cause) {
      setError(cause instanceof Error && cause.message ? cause.message : '账号服务暂不可用，请稍后再试。');
    } finally {
      setPending(false);
    }
  }

  if (recoveryCode !== null) {
    return (
      <RecoveryCode
        code={recoveryCode}
        onContinue={() => {
          setRecoveryCode(null);
          onAuthenticated();
        }}
      />
    );
  }

  return (
    <section className="panel stack" aria-labelledby={titleId}>
      <h2 id={titleId}>CareerPilot 账号</h2>
      <div className="row" role="group" aria-label="账号操作">
        {modes.map(({ mode: candidate, label }) => (
          <button
            key={candidate}
            type="button"
            className={mode === candidate ? 'primary' : 'secondary'}
            aria-pressed={mode === candidate}
            disabled={pending}
            onClick={() => {
              setMode(candidate);
              setError('');
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <form key={mode} className="stack" onSubmit={submit} aria-busy={pending}>
        <label className="stack">
          用户名
          <input
            name="username"
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
          <small id={usernameHint} className="muted">3 到 32 位英文字母、数字或下划线。</small>
        )}
        {mode === 'recover' && (
          <label className="stack">
            恢复码
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
        <label className="stack">
          {mode === 'recover' ? '新密码' : '密码'}
          <input
            type="password"
            name="password"
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            minLength={mode === 'login' ? undefined : 12}
            maxLength={128}
            aria-describedby={mode === 'login' ? undefined : passwordHint}
            disabled={pending}
            required
          />
        </label>
        {mode !== 'login' && (
          <small id={passwordHint} className="muted">至少 12 个字符。</small>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <div className="actions">
          <button type="submit" className="primary" disabled={pending}>
            {pending ? '处理中…' : submitLabels[mode]}
          </button>
        </div>
      </form>
    </section>
  );
}
