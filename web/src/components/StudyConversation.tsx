import { useT } from '../i18n';
import { forwardRef, type ComponentPropsWithoutRef, type KeyboardEvent, type ReactNode } from 'react';

export function submitKey(event: KeyboardEvent<HTMLTextAreaElement>, send: () => void) {
  if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
    event.preventDefault();
    if (!event.repeat) send();
  }
}

const dayGroups = ["study.today", "study.yesterday", "study.lastSevenDays", "study.older"] as const;
function dayGroup(value: string) {
  const date = new Date(value);
  const midnight = (d: Date) => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  const days = (midnight(new Date()) - midnight(date)) / 86400000;
  return dayGroups[days <= 0 ? 0 : days === 1 ? 1 : days <= 7 ? 2 : 3];
}

export function StudyTurn({ speaker, className = '', children, ...article }: ComponentPropsWithoutRef<'article'> & {
  speaker: string;
}) {
  return <article {...article} className={['study-turn', speaker, className].filter(Boolean).join(' ')}>{children}</article>;
}

export const StudyComposerInput = forwardRef<HTMLTextAreaElement, ComponentPropsWithoutRef<'textarea'> & {
  onSend: () => void;
}>(function StudyComposerInput({ onSend, ...props }, ref) {
  return <textarea {...props} ref={ref} onKeyDown={event => submitKey(event, onSend)} />;
});

export function StudyHistory<Row extends { id: number; updated_at?: string }>({ rows, renderRow }: {
  rows: Row[];
  renderRow: (row: Row) => ReactNode;
}) {
  const t = useT();
  return <>{dayGroups.map(group => {
    const items = rows.filter(row => (row.updated_at ? dayGroup(row.updated_at) : 'study.older') === group);
    return items.length > 0 && <section key={group}><h2>{t(group)}</h2>{items.map(renderRow)}</section>;
  })}</>;
}
