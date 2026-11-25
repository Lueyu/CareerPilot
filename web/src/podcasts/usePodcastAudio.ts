import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { PodcastEpisode } from '../api/podcasts';

type Source = {
  episode: PodcastEpisode;
  controller: AbortController;
  position: number;
  url?: string;
  wantsPlay: boolean;
  lastSaved: number;
  started: boolean;
  fetching: boolean;
};

type AudioOptions = {
  load: (id: number, signal: AbortSignal) => Promise<Blob>;
  progress: (id: number, position: number, flush: boolean) => void;
  ended: () => void;
};

export function usePodcastAudio(options: AudioOptions) {
  const element = useRef<HTMLAudioElement>(null);
  const active = useRef<Source | null>(null);
  const callbacks = useRef(options);
  const [episode, setEpisode] = useState<PodcastEpisode>();
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  useLayoutEffect(() => { callbacks.current = options; }, [options]);

  function checkpoint(flush: boolean) {
    const audio = element.current;
    const source = active.current;
    if (!source?.url || !source.started || !audio || audio.ended) return;
    const now = Math.floor(audio.currentTime);
    if (flush || Math.abs(now - source.lastSaved) >= 3) {
      source.lastSaved = now;
      callbacks.current.progress(source.episode.id, now, flush);
    }
  }

  function dispose() {
    const source = active.current;
    active.current = null;
    source?.controller.abort();
    const audio = element.current;
    if (audio) {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    }
    if (source?.url) URL.revokeObjectURL(source.url);
  }

  function lock() {
    dispose();
    setEpisode(undefined);
    setPosition(0);
    setDuration(0);
    setPlaying(false);
    setLoading(false);
    setError('');
  }

  function playSource(source: Source) {
    if (!source.url || !element.current || active.current !== source) return;
    setError('');
    void element.current.play().catch(error => {
      if (active.current !== source || error?.name === 'AbortError') return;
      source.wantsPlay = false;
      setPlaying(false);
      setError(error?.name === 'NotAllowedError' ? '点播放继续收听' : '音频无法播放，请重试');
    });
  }

  async function fetchSource(source: Source) {
    if (source.fetching) return;
    source.fetching = true;
    setLoading(true);
    setError('');
    try {
      const blob = await callbacks.current.load(source.episode.id, source.controller.signal);
      if (source.controller.signal.aborted || active.current !== source || !element.current) return;
      if (!blob.size) throw new Error('节目音频为空');
      source.url = URL.createObjectURL(blob);
      element.current.src = source.url;
      if (source.wantsPlay) playSource(source);
    } catch (error) {
      if (active.current === source && !source.controller.signal.aborted) {
        setError(error instanceof Error ? error.message : '音频读取失败，请重试');
      }
    } finally {
      source.fetching = false;
      if (active.current === source) setLoading(false);
    }
  }

  function select(next: PodcastEpisode, startPosition: number, autoplay: boolean, preload = false) {
    if (active.current?.episode.id === next.id) {
      if (autoplay) play();
      return;
    }
    checkpoint(true);
    dispose();
    const resumeAt = startPosition >= next.duration_seconds && next.duration_seconds > 0 ? 0 : startPosition;
    const source: Source = {
      episode: next, controller: new AbortController(), position: resumeAt, wantsPlay: autoplay,
      lastSaved: resumeAt, started: false, fetching: false,
    };
    active.current = source;
    setEpisode(next);
    setPosition(resumeAt);
    setDuration(next.duration_seconds);
    setPlaying(false);
    setLoading(false);
    setError('');
    if (autoplay || preload) void fetchSource(source);
  }

  function play() {
    const source = active.current;
    if (!source) return;
    source.wantsPlay = true;
    if (source.url) {
      if (element.current?.ended) {
        element.current.currentTime = 0;
        source.position = 0;
      }
      playSource(source);
    } else {
      void fetchSource(source);
    }
  }

  function wantsPlayback() {
    return !!active.current?.wantsPlay;
  }

  function pause() {
    if (active.current) active.current.wantsPlay = false;
    element.current?.pause();
  }

  function seek(time: number) {
    if (!active.current || !Number.isFinite(time)) return;
    const next = Math.min(Math.max(0, time), duration || 0);
    active.current.position = next;
    if (element.current && active.current.url) element.current.currentTime = next;
    active.current.lastSaved = next;
    setPosition(next);
    callbacks.current.progress(active.current.episode.id, next, true);
  }

  useEffect(() => {
    const audio = element.current;
    if (!audio) return;
    const metadata = () => {
      const source = active.current;
      if (!source?.url) return;
      const length = Number.isFinite(audio.duration) ? audio.duration : source.episode.duration_seconds;
      setDuration(length);
      audio.currentTime = Math.min(source.position, length);
    };
    const onPlay = () => {
      if (!active.current?.url) return;
      active.current.started = true;
      setPlaying(true);
    };
    const onPause = () => {
      setPlaying(false);
      if (active.current?.started && audio.paused) active.current.wantsPlay = false;
      checkpoint(true);
    };
    const onTime = () => {
      if (!active.current?.url || audio.readyState === 0) return;
      setPosition(audio.currentTime);
      checkpoint(false);
    };
    const onEnd = () => {
      if (!active.current?.url) return;
      setPlaying(false);
      callbacks.current.progress(active.current.episode.id, 0, true);
      callbacks.current.ended();
    };
    const onError = () => {
      const source = active.current;
      if (!source?.url) return;
      URL.revokeObjectURL(source.url);
      source.url = undefined;
      source.wantsPlay = false;
      setPlaying(false);
      setError('音频无法播放，请重试');
    };
    const onPageHide = () => checkpoint(true);
    const visibility = () => { if (document.visibilityState === 'hidden') checkpoint(true); };
    audio.addEventListener('loadedmetadata', metadata);
    audio.addEventListener('playing', onPlay);
    audio.addEventListener('pause', onPause);
    audio.addEventListener('timeupdate', onTime);
    audio.addEventListener('ended', onEnd);
    audio.addEventListener('error', onError);
    window.addEventListener('pagehide', onPageHide);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      audio.removeEventListener('loadedmetadata', metadata);
      audio.removeEventListener('playing', onPlay);
      audio.removeEventListener('pause', onPause);
      audio.removeEventListener('timeupdate', onTime);
      audio.removeEventListener('ended', onEnd);
      audio.removeEventListener('error', onError);
      window.removeEventListener('pagehide', onPageHide);
      document.removeEventListener('visibilitychange', visibility);
      dispose();
    };
  }, []);

  return { element, episode, position, duration, playing, error, loading, select, play, pause, seek, lock, wantsPlayback };
}
