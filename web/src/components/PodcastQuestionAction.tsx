import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useT } from '../i18n';

export function usePodcastSelection() {
  const [ids, setIds] = useState<number[] | null>(null);
  function toggle(id: number) {
    setIds(current => {
      if (!current) return current;
      return current.includes(id) ? current.filter(item => item !== id) : [...current, id];
    });
  }
  return { ids, toggle, begin: () => setIds([]), cancel: () => setIds(null) };
}

type Selection = ReturnType<typeof usePodcastSelection>;

export function PodcastSelectionBar({ selection }: { selection: Selection }) {
  const t = useT();
  if (!selection.ids) return <button type="button" className="btn btn-outline btn-sm" onClick={selection.begin}>
    {t('bank.selectForPodcast')}
  </button>;
  return <div className="bank-actions">
    {selection.ids.length > 0
      ? <Link className="btn btn-primary btn-sm" to={'/podcasts?questions=' + selection.ids.join(',')}>
        {t('bank.preparePodcasts', { count: selection.ids.length })}
      </Link>
      : <span className="hint">{t('bank.pickPodcastQuestions')}</span>}
    <button type="button" className="btn btn-ghost btn-sm" onClick={selection.cancel}>{t('bank.cancelPodcastSelection')}</button>
  </div>;
}

export function PodcastQuestionAction({ id, title, selection }: { id: number; title: string; selection?: Selection }) {
  const t = useT();
  if (selection?.ids) return <label className="btn btn-outline btn-sm">
    <input type="checkbox" checked={selection.ids.includes(id)} onChange={() => selection.toggle(id)}
      aria-label={t('bank.selectPodcastQuestion', { title })} /> {t('bank.selectQuestionForPodcast')}
  </label>;
  return <Link to={'/podcasts?questions=' + id} className="btn btn-outline btn-sm">{t('bank.makePodcast')}</Link>;
}
