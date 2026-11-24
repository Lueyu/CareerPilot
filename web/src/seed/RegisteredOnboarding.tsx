import { useLocale, useT } from '../i18n';
import { renderMessage } from './api';
import { directionExample, directionLabel, useLibraryRole } from "./libraryRole";
import "../styles/bank-presentation.css";

export function RegisteredOnboarding({ onComplete }: { onComplete: () => void }) {
  const t = useT();
  const locale = useLocale();
  const {
    roles, active, direction, roleInput, setRoleInput, jd, setJd,
    busy, error, status, setRetry, saveRole, selectDirection,
  } = useLibraryRole();
  const saved = active && active.role === roleInput.trim() && (active.jd ?? "") === jd.trim();

  return (
    <section className="card registered-onboarding" aria-labelledby="registered-onboarding-title">
      <h1 id="registered-onboarding-title">{t('registered.targetRolePrompt')}</h1>
      <form className="registered-onboarding-fields" onSubmit={saveRole}>
        <label className="field">
          {t('registered.targetRole')}
          <input
            className="inp"
            value={roleInput}
            onChange={(event) => {
              const value = event.target.value;
              setRoleInput(value);
              setJd(roles?.roles.find((role) => role.role === value.trim())?.jd ?? "");
            }}
            placeholder={t('registered.onboardingRolePlaceholder')}
            maxLength={120}
            disabled={!roles || !!busy}
            required
          />
        </label>
        <label className="field">
          {t('registered.optionalJobRequirements')}
          <textarea
            className="inp"
            value={jd}
            onChange={(event) => setJd(event.target.value)}
            placeholder={t('registered.jobRequirementsPlaceholder')}
            rows={3}
            maxLength={6000}
            disabled={!roles || !!busy}
          />
        </label>
        <button type="submit" className="btn btn-outline" disabled={!roles || !roleInput.trim() || !!busy}>
          {t('registered.saveRole')}
        </button>
      </form>
      {active && saved && (
        <fieldset className="registered-onboarding-directions">
          <legend>{t('registered.practiceTopic')}</legend>
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
                {directionLabel(item, t)}
              </button>
            ))}
          </div>
        </fieldset>
      )}
      {saved && direction && (
        <p className="registered-onboarding-example"><strong>{t('registered.sampleQuestion')}</strong>{directionExample(direction, locale)}</p>
      )}
      {busy && <p role="status">{t(busy)}</p>}
      {!roles && !error && <p role="status">{t('registered.loading')}</p>}
      {status && <p role="status">{t(status)}</p>}
      {error && <p className="error" role="alert">{renderMessage(error, t)}</p>}
      {!roles && error && (
        <button type="button" className="btn btn-outline" onClick={() => setRetry((value) => value + 1)}>
          {t('registered.reloadRoles')}
        </button>
      )}
      <button type="button" className="btn btn-primary registered-onboarding-continue" disabled={!saved || !direction || !!busy} onClick={onComplete}>
        {t('registered.enterWorkspace')}
      </button>
    </section>
  );
}
