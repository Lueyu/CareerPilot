import { useLibraryRole } from "./libraryRole";
import "../styles/bank-presentation.css";

export function RegisteredOnboarding({ onComplete }: { onComplete: () => void }) {
  const {
    roles, active, direction, roleInput, setRoleInput, jd, setJd,
    busy, error, status, setRetry, saveRole, selectDirection,
  } = useLibraryRole();
  const saved = active && active.role === roleInput.trim() && (active.jd ?? "") === jd.trim();

  return (
    <section className="card registered-onboarding" aria-labelledby="registered-onboarding-title">
      <h1 id="registered-onboarding-title">你想准备什么岗位？</h1>
      <form className="registered-onboarding-fields" onSubmit={saveRole}>
        <label className="field">
          目标岗位
          <input
            className="inp"
            value={roleInput}
            onChange={(event) => {
              const value = event.target.value;
              setRoleInput(value);
              setJd(roles?.roles.find((role) => role.role === value.trim())?.jd ?? "");
            }}
            placeholder="例如 后端开发、产品运营"
            maxLength={120}
            disabled={!roles || !!busy}
            required
          />
        </label>
        <label className="field">
          招聘要求（选填）
          <textarea
            className="inp"
            value={jd}
            onChange={(event) => setJd(event.target.value)}
            placeholder="贴入岗位职责或你想重点准备的要求"
            rows={3}
            maxLength={6000}
            disabled={!roles || !!busy}
          />
        </label>
        <button type="submit" className="btn btn-outline" disabled={!roles || !roleInput.trim() || !!busy}>
          保存岗位
        </button>
      </form>
      {active && saved && (
        <fieldset className="registered-onboarding-directions">
          <legend>练习方向</legend>
          <div className="registered-onboarding-chips">
            {active.directions.map((item) => (
              <button
                key={item.name}
                type="button"
                className={'btn btn-sm ' + (direction === item ? 'btn-primary' : 'btn-outline')}
                aria-pressed={direction === item}
                disabled={!!busy}
                onClick={() => void selectDirection(item.name)}
              >
                {item.name}
              </button>
            ))}
          </div>
        </fieldset>
      )}
      {saved && direction && (
        <p className="registered-onboarding-example"><strong>例题</strong>{direction.example}</p>
      )}
      {busy && <p role="status">{busy}</p>}
      {!roles && !error && <p role="status">正在加载…</p>}
      {status && <p role="status">{status}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {!roles && error && (
        <button type="button" className="btn btn-outline" onClick={() => setRetry((value) => value + 1)}>
          重新加载岗位
        </button>
      )}
      <button type="button" className="btn btn-primary registered-onboarding-continue" disabled={!saved || !direction || !!busy} onClick={onComplete}>
        进入工作空间
      </button>
    </section>
  );
}
