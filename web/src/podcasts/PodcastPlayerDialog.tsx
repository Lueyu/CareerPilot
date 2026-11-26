import { useEffect, useRef, type PointerEvent, type ReactNode } from 'react';
import type { PodcastEpisode } from '../api/podcasts';

export function podcastTime(value: number) {
  const seconds = Number.isFinite(value) ? Math.floor(value) : 0;
  return Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
}

export function PodcastDialog({ name, open, onClose, children }: {
  name: string; open: boolean; onClose: () => void; children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
  }, [open]);
  return <dialog ref={dialog} className="podcast-dialog" aria-label={name} onCancel={event => {
    event.preventDefault();
    onClose();
  }}>{open && children}</dialog>;
}

export function PodcastCards({ episode, previous, next, step }: {
  episode: PodcastEpisode;
  previous?: PodcastEpisode;
  next?: PodcastEpisode;
  step: (direction: -1 | 1) => void;
}) {
  const gesture = useRef<{ id: number; x: number; y: number }>();
  function start(event: PointerEvent<HTMLDivElement>) {
    if (!event.isPrimary || event.button !== 0) return;
    gesture.current = { id: event.pointerId, x: event.clientX, y: event.clientY };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function finish(event: PointerEvent<HTMLDivElement>) {
    const drag = gesture.current;
    if (drag?.id !== event.pointerId) return;
    gesture.current = undefined;
    const horizontal = event.clientX - drag.x;
    const vertical = event.clientY - drag.y;
    if (Math.abs(horizontal) >= 36 && Math.abs(horizontal) > 1.35 * Math.abs(vertical)) {
      step(horizontal > 0 ? -1 : 1);
    }
  }

  return <div className="podcast-cards" aria-label="节目唱片">
    <button type="button" className="podcast-neighbor podcast-neighbor-prev" disabled={!previous}
      onClick={() => step(-1)} aria-label={previous ? '上一集，' + previous.title : '没有上一集'} hidden={!previous}>‹</button>
    <div className="podcast-cover" onPointerDown={start} onPointerUp={finish}
      onPointerCancel={() => { gesture.current = undefined; }} onLostPointerCapture={() => { gesture.current = undefined; }}>
      <div className="podcast-disc" aria-hidden="true"><span>♫</span></div>
      <span className="podcast-topic">{episode.topic}</span>
    </div>
    <button type="button" className="podcast-neighbor podcast-neighbor-next" disabled={!next}
      onClick={() => step(1)} aria-label={next ? '下一集，' + next.title : '没有下一集'} hidden={!next}>›</button>
  </div>;
}
