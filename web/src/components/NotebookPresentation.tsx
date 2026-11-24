import { useLocale, useT } from '../i18n';
import type { ReactNode } from 'react';

export type NotebookText = { title: string; body: string };
export type NotebookSummary = { id: string | number; title: string; updated_at?: string };

export function NotebookFields({ text, disabled, onChange }: {
  text: NotebookText;
  disabled: boolean;
  onChange: (text: NotebookText) => void;
}) {
  const t = useT();

  return <>
    <label className="study-notebook-field">{t("study.title")}
      <input className="inp" required value={text.title} disabled={disabled}
        onChange={event => onChange({ ...text, title: event.target.value })} />
    </label>
    <label className="study-notebook-field">{t("study.body")}
      <textarea aria-label={t("study.body")} className="inp" rows={12} value={text.body} disabled={disabled}
        onChange={event => onChange({ ...text, body: event.target.value })} />
    </label>
  </>;
}

export function NotebookList({ notes, renderLink }: {
  notes: NotebookSummary[];
  renderLink: (note: NotebookSummary, content: ReactNode) => ReactNode;
}) {
  const locale = useLocale();
  return <ul className="study-notebook-list study-notebook-library">{notes.map(note => (
    <li key={note.id}>{renderLink(note, <>
      <strong>{note.title}</strong><span className="study-notebook-library-date">
        {note.updated_at && <time dateTime={note.updated_at}>{new Date(note.updated_at).toLocaleDateString(locale)}</time>}
        <span aria-hidden="true"> →</span>
      </span>
    </>)}</li>
  ))}</ul>;
}
