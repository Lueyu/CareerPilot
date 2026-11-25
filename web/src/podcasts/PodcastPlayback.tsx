import {
  createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode,
} from 'react';
import type { Familiarity, PodcastEpisode } from '../api/podcasts';
import {
  exportOfflinePlaylist, offlineLibrary, offlinePodcastUrl, podcastLibraryChanged, podcastPacksChanged,
} from './podcastOfflineMedia';
import {
  identityIsCurrent, podcastStorageScope, rememberOfflineIdentity, watchPodcastIdentity, type PodcastIdentity,
} from './podcastOfflineIdentity';
import { PodcastCards, PodcastDialog, podcastTime } from './PodcastPlayerDialog';
import {
  adjacentEpisode, playbackScopeKey, scopeEpisodes, shuffleRound,
  type PlaybackLibrary, type PlaybackScope, type StartPodcastPlayback,
} from './podcastQueue';
import {
  flushPodcastChanges, readPodcastResume, readPodcastWorkspace, readSavedMedia, removePodcastPack, rememberPodcastResume, savePodcastChange,
  type EpisodePatch, type OfflinePlaylist, type Resume,
} from './podcastStore';
import { usePodcastAudio } from './usePodcastAudio';
import './podcastPlayer.css';

type Player = {
  play: StartPodcastPlayback;
  open: () => void;
};
const PlayerContext = createContext<Player | null>(null);
const allScope: PlaybackScope = { kind: 'all' };
const emptyLibrary: PlaybackLibrary = { episodes: [], playlists: [] };
const noEpisodes = '这个范围没有可播放的节目，请换一个范围。';

export function usePodcastPlayer() {
  const player = useContext(PlayerContext);
  if (!player) throw new Error('PodcastPlayerProvider is required');
  return player;
}

type WorkspaceAccess = {
  isCurrent: () => boolean;
  library: () => Promise<PlaybackLibrary>;
  audio: (id: number, signal: AbortSignal) => Promise<Blob>;
  script: (id: number) => Promise<{ script: string }>;
  update: (id: number, patch: EpisodePatch, signal: AbortSignal) => Promise<unknown>;
  save: (episodes: PodcastEpisode[], name: string, scope: PlaybackScope) => Promise<void>;
  export: (episodes: PodcastEpisode[], name: string) => Promise<void>;
};

export function PodcastPlaybackProvider({ children, identity, access }: {
  children: ReactNode; identity: PodcastIdentity; access?: WorkspaceAccess;
}) {
  return <ScopedPlayer key={podcastStorageScope(identity) + ':' + identity.epoch} identity={identity} access={access}>
    {children}
  </ScopedPlayer>;
}

function ScopedPlayer({ identity, access, children }: { identity: PodcastIdentity; access?: WorkspaceAccess; children: ReactNode }) {
  const offline = !access;
  const [library, setLibrary] = useState<PlaybackLibrary>();
  const [fallback, setFallback] = useState<PlaybackLibrary>(emptyLibrary);
  const [patches, setPatches] = useState<Record<number, EpisodePatch>>({});
  const [packs, setPacks] = useState<OfflinePlaylist[]>([]);
  const [scope, setScope] = useState<PlaybackScope>(allScope);
  const [selection, setSelection] = useState<Extract<PlaybackScope, { kind: 'selection' }>>();
  const [order, setOrder] = useState<'ordered' | 'shuffle'>('ordered');
  const [shuffled, setShuffled] = useState<number[]>([]);
  const [repeat, setRepeat] = useState(false);
  const [visible, setVisible] = useState(false);
  const [managing, setManaging] = useState(false);
  const [transcript, setTranscript] = useState<null | { id: number; text?: string }>(null);
  const [status, setStatus] = useState('');
  const [pending, setPending] = useState(false);
  const [locked, setLocked] = useState(false);
  const lifetime = useRef<AbortController>();
  const transcriptRequest = useRef<AbortController>();
  const picked = useRef(false);
  const authorized = useRef(offline);
  const syncTimer = useRef<number>();
  const syncRunning = useRef(false);
  const syncAgain = useRef(false);
  const refreshId = useRef(0);
  const changeRevision = useRef(0);
  const mediaActions = useRef<(action: MediaSessionAction, details: MediaSessionActionDetails) => void>();

  function isCurrent() {
    return !lifetime.current?.signal.aborted && identityIsCurrent(identity) &&
      (!access || access.isCurrent());
  }

  async function sourceAudio(id: number, signal: AbortSignal) {
    if (!isCurrent()) throw new Error('工作区已切换');
    const data = await readSavedMedia(identity, id).catch(error => {
      if (offline) throw error;
      return undefined;
    });
    signal.throwIfAborted();
    if (!isCurrent()) throw new Error('工作区已切换');
    if (data?.audio.size) return data.audio;
    if (!access) throw new Error('离线音频已被移除，请联网后重新保存');
    const audio = await access.audio(id, signal);
    signal.throwIfAborted();
    if (!isCurrent()) throw new Error('工作区已切换');
    return audio;
  }

  const audio = usePodcastAudio({
    load: sourceAudio,
    progress: (id, position, flush) =>
      persist(id, { position_seconds: position }, { id, position }, flush),
    ended: () => {
      if (repeat) audio.play();
      else step(1, true);
    },
  });

  const activeLibrary = useMemo(() => {
    const base = library ?? fallback;
    return {
      ...base,
      episodes: base.episodes.map(episode => ({ ...episode, ...patches[episode.id] })),
    };
  }, [library, fallback, patches]);
  const episode = activeLibrary.episodes.find(item => item.id === audio.episode?.id) ?? audio.episode;
  const eligible = scopeEpisodes(activeLibrary, scope);
  const orderIds = order === 'shuffle' ? shuffled
    : (scope.kind === 'unfamiliar' ? scopeEpisodes(activeLibrary, allScope) : eligible).map(item => item.id);
  const previous = adjacentEpisode(orderIds, eligible, episode?.id, -1);
  const next = adjacentEpisode(orderIds, eligible, episode?.id, 1);
  const topics = [...new Set(activeLibrary.episodes.filter(item => item.status === 'ready').map(item => item.topic))];
  const playlistNameCounts = new Map<string, number>();
  for (const list of activeLibrary.playlists) {
    playlistNameCounts.set(list.name, (playlistNameCounts.get(list.name) ?? 0) + 1);
  }
  const playlistNamePositions = new Map<string, number>();
  const scopeOptions: { scope: PlaybackScope; name: string; label?: string }[] = [
    { scope: allScope, name: '全部节目' },
    { scope: { kind: 'unfamiliar' }, name: '不熟悉的节目', label: '不熟悉的随机听' },
    ...topics.map(topic => ({ scope: { kind: 'topic', topic } as const, name: topic, label: '主题 · ' + topic })),
    ...activeLibrary.playlists.map(({ id, name }) => {
      const count = playlistNameCounts.get(name) ?? 1;
      const position = (playlistNamePositions.get(name) ?? 0) + 1;
      playlistNamePositions.set(name, position);
      const suffix = count > 1 ? '（' + position + '/' + count + '）' : '';
      return { scope: { kind: 'playlist', id } as const, name, label: '歌单 · ' + name + suffix };
    }),
  ];
  if (selection) scopeOptions.push({
    scope: selection, name: selection.source === 'search' ? '搜索结果' : '本次制作',
  });
  const scopeValue = playbackScopeKey(scope);
  if (!scopeOptions.some(option => playbackScopeKey(option.scope) === scopeValue)) {
    scopeOptions.push({ scope, name: scope.kind === 'topic' ? scope.topic : '当前歌单' });
  }
  const scopeName = scopeOptions.find(option => playbackScopeKey(option.scope) === scopeValue)?.name ?? '全部节目';

  function syncSoon(delay = 0) {
    if (!access || !authorized.current || !isCurrent() || !navigator.onLine) return;
    if (delay && syncTimer.current !== undefined) return;
    clearTimeout(syncTimer.current);
    syncTimer.current = window.setTimeout(() => {
      syncTimer.current = undefined;
      if (syncRunning.current) {
        syncAgain.current = true;
        return;
      }
      const signal = lifetime.current?.signal;
      if (!signal || !isCurrent()) return;
      syncRunning.current = true;
      void flushPodcastChanges(identity, signal, (id, patch, requestSignal) => {
        requestSignal.throwIfAborted();
        if (!isCurrent()) throw new Error('工作区已切换');
        return access.update(id, patch, requestSignal);
      }).then(sent => {
        if (sent && isCurrent()) {
          window.dispatchEvent(new CustomEvent(podcastLibraryChanged, { detail: { source: 'player' } }));
          void refresh();
        }
      }).catch(() => { /* Pending changes stay queued for the next connection. */ }).finally(() => {
        syncRunning.current = false;
        if (syncAgain.current) {
          syncAgain.current = false;
          syncSoon(0);
        }
      });
    }, delay);
  }

  function persist(id: number, patch: EpisodePatch, resume?: Resume, flush = true) {
    if (!isCurrent()) return;
    const checkpoint = resume && rememberPodcastResume(identity, { ...resume, sendPosition: patch.position_seconds !== undefined });
    if (Object.keys(patch).length) {
      changeRevision.current++;
      setPatches(value => ({ ...value, [id]: { ...value[id], ...patch } }));
    }
    void savePodcastChange(identity, id, patch, checkpoint).then(() => {
      if (Object.keys(patch).length) syncSoon(flush ? 0 : 4500);
    }).catch(() => {
      if (isCurrent()) setStatus('这台设备未能保存收听进度或熟悉度。');
    });
  }

  async function refresh(restore = false) {
    if (!isCurrent()) return;
    const signal = lifetime.current?.signal;
    const request = ++refreshId.current;
    const knownRevision = changeRevision.current;
    try {
      const data = access ? await access.library() : await offlineLibrary(identity);
      if (signal?.aborted || !isCurrent() || request !== refreshId.current) return;
      authorized.current = true;
      setLibrary(data);
      const local = await readPodcastWorkspace(identity).catch(() => undefined);
      if (signal?.aborted || !isCurrent() || request !== refreshId.current) return;
      const localPatches = offline ? local?.overrides : Object.fromEntries(Object.entries(local?.pending ?? {})
        .map(([id, entry]) => [id, entry.patch]));
      setPacks(local?.packs ?? []);
      setPatches(previous => knownRevision === changeRevision.current
        ? localPatches ?? {}
        : { ...localPatches, ...previous });
      if (!offline && local?.packs.length) {
        try { rememberOfflineIdentity(identity); } catch { /* Online playback can proceed in a restricted browser. */ }
      }
      if (restore && !picked.current) {
        const journal = readPodcastResume(identity);
        const useJournal = journal && journal.updatedAt > (local?.resume?.updatedAt ?? 0);
        const resting = useJournal ? journal : local?.resume;
        const last = data.episodes.find(item => item.id === resting?.id && item.status === 'ready');
        if (last && resting) {
          picked.current = true;
          if (useJournal && journal.sendPosition) {
            persist(last.id, { position_seconds: resting.position }, resting);
          }
          audio.select(last, resting.position, false, true);
        }
      }
      syncSoon();
    } catch {
      if (offline && isCurrent()) setStatus('无法读取这台设备上保存的播客。');
    }
  }

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const controller = new AbortController();
    lifetime.current = controller;
    const verify = () => {
      if (isCurrent()) return;
      controller.abort();
      transcriptRequest.current?.abort();
      clearTimeout(syncTimer.current);
      setVisible(false);
      setLocked(true);
      audio.lock();
    };
    const unwatch = watchPodcastIdentity(verify);
    const reconnect = () => { void refresh(true); };
    const packsChanged = () => { void refresh(true); };
    const libraryChanged = (event: Event) => {
      if (!offline && (!(event instanceof CustomEvent) || event.detail?.source !== 'player')) void refresh(true);
    };
    window.addEventListener('online', reconnect);
    window.addEventListener(podcastPacksChanged, packsChanged);
    window.addEventListener(podcastLibraryChanged, libraryChanged);
    if (offline || readPodcastResume(identity)) {
      void refresh(true);
    } else if (typeof indexedDB !== 'undefined') {
      void readPodcastWorkspace(identity).then(local => {
        if (!controller.signal.aborted && (local.resume || local.packs.length || Object.keys(local.pending).length)) {
          return refresh(true);
        }
      }).catch(() => undefined);
    }
    return () => {
      controller.abort();
      transcriptRequest.current?.abort();
      clearTimeout(syncTimer.current);
      unwatch();
      window.removeEventListener('online', reconnect);
      window.removeEventListener(podcastPacksChanged, packsChanged);
      window.removeEventListener(podcastLibraryChanged, libraryChanged);
    };
  }, [identity.workspace, identity.epoch, offline]);

  function pick(item: PodcastEpisode, autoplay: boolean) {
    picked.current = true;
    if (audio.episode?.id === item.id) {
      if (autoplay) audio.play();
      return;
    }
    transcriptRequest.current?.abort();
    setTranscript(null);
    setStatus('');
    const position = item.position_seconds ?? 0;
    audio.select(item, position, autoplay);
    persist(item.id, {}, { id: item.id, position });
  }

  function step(direction: -1 | 1, autoplay = audio.wantsPlayback()) {
    if (!isCurrent()) return;
    const item = direction === -1 ? previous : next;
    if (item) pick(item, autoplay);
    else if (!eligible.length) setStatus(noEpisodes);
  }

  function chooseScope(nextScope: PlaybackScope) {
    const candidates = scopeEpisodes(activeLibrary, nextScope);
    const nextOrder = nextScope.kind === 'unfamiliar' ? 'shuffle' : order;
    const round = nextOrder === 'shuffle' ? shuffleRound(candidates.map(item => item.id), episode?.id) : [];
    setScope(nextScope);
    setOrder(nextOrder);
    setShuffled(round);
    setRepeat(false);
    setStatus(candidates.length ? '' : noEpisodes);
    if (candidates.length && !candidates.some(item => item.id === episode?.id)) {
      const first = nextOrder === 'shuffle' ? candidates.find(item => item.id === round[0]) : candidates[0];
      if (first) pick(first, audio.wantsPlayback());
    }
  }

  const play: StartPodcastPlayback = (item, queue, nextScope) => {
    if (item.status !== 'ready' || !isCurrent()) return;
    const launch: PlaybackLibrary = {
      episodes: queue.map(candidate => ({ ...candidate, ...patches[candidate.id] })),
      playlists: nextScope.kind === 'playlist' ? [{
        id: nextScope.id,
        name: activeLibrary.playlists.find(list => list.id === nextScope.id)?.name ?? '当前歌单',
        episode_ids: queue.map(candidate => candidate.id),
      }] : [],
    };
    const candidates = scopeEpisodes(launch, nextScope);
    const shuffle = nextScope.kind === 'unfamiliar';
    const round = shuffle ? shuffleRound(candidates.map(candidate => candidate.id), item.id) : [];
    const start = candidates.find(candidate => candidate.id === item.id) ??
      (shuffle ? candidates.find(candidate => candidate.id === round[0]) : candidates[0]);
    setLibrary(data => {
      if (!data) return undefined;
      const known = new Set(scopeEpisodes(data, nextScope).map(candidate => candidate.id));
      return candidates.every(candidate => known.has(candidate.id)) ? data : undefined;
    });
    setFallback(launch);
    setSelection(nextScope.kind === 'selection' ? nextScope : undefined);
    setScope(nextScope);
    setOrder(shuffle ? 'shuffle' : 'ordered');
    setShuffled(round);
    setRepeat(false);
    if (start) pick(start, true);
    else setStatus(noEpisodes);
    setVisible(true);
    void refresh();
  };

  function chooseOrder(nextOrder: 'ordered' | 'shuffle') {
    if (nextOrder !== order && nextOrder === 'shuffle') {
      setShuffled(shuffleRound(eligible.map(item => item.id), episode?.id));
    }
    setOrder(nextOrder);
    setRepeat(false);
  }

  function open() {
    if (!isCurrent()) return;
    setVisible(true);
    void refresh(true);
  }

  function close() {
    setVisible(false);
    setManaging(false);
    transcriptRequest.current?.abort();
    setTranscript(null);
  }

  async function showTranscript() {
    if (!episode || !isCurrent()) return;
    if (transcript) {
      transcriptRequest.current?.abort();
      setTranscript(null);
      return;
    }
    const id = episode.id;
    const controller = new AbortController();
    transcriptRequest.current?.abort();
    transcriptRequest.current = controller;
    setTranscript({ id });
    try {
      const saved = await readSavedMedia(identity, id).catch(() => undefined);
      if (controller.signal.aborted || !isCurrent()) return;
      const text = saved?.script ?? (access ? (await access.script(id)).script : '这集逐字稿未保存。');
      if (!controller.signal.aborted && isCurrent()) setTranscript({ id, text });
    } catch {
      if (!controller.signal.aborted && isCurrent()) setTranscript({ id, text: '暂时无法读取逐字稿，请重试。' });
    }
  }

  function mark(value: Familiarity) {
    if (!episode || episode.familiarity === value) return;
    if (scope.kind === 'unfamiliar' && value === 'familiar') setRepeat(false);
    persist(episode.id, { familiarity: value });
    setStatus(value === 'familiar' ? '已标为熟悉' : '已标为不熟悉');
  }

  async function operate(action: () => Promise<void>, message: string) {
    if (pending || !isCurrent()) return;
    setPending(true);
    setStatus('');
    try {
      await action();
      if (isCurrent()) setStatus(message);
    } catch (error) {
      if (isCurrent()) setStatus(error instanceof Error ? error.message : '操作失败，请重试');
    } finally {
      setPending(false);
    }
  }

  useEffect(() => {
    mediaActions.current = (action, detail) => {
      if (!isCurrent()) return;
      if (action === 'play') audio.play();
      if (action === 'pause' || action === 'stop') audio.pause();
      if (action === 'previoustrack') step(-1, true);
      if (action === 'nexttrack') step(1, true);
      if (action === 'seekbackward') audio.seek(audio.position - (detail.seekOffset ?? 15));
      if (action === 'seekforward') audio.seek(audio.position + (detail.seekOffset ?? 15));
      if (action === 'seekto' && detail.seekTime !== undefined) audio.seek(detail.seekTime);
    };
  });

  useEffect(() => {
    const session = navigator.mediaSession;
    if (!session || typeof MediaMetadata === 'undefined' || !episode || locked) return;
    session.metadata = new MediaMetadata({ title: episode.title, artist: 'CareerPilot', album: episode.topic });
    const actions: MediaSessionAction[] = [
      'play', 'pause', 'stop', 'previoustrack', 'nexttrack', 'seekbackward', 'seekforward', 'seekto',
    ];
    const available = (action: MediaSessionAction) => action === 'previoustrack' ? !!previous : action === 'nexttrack' ? !!next : true;
    for (const action of actions) {
      try { session.setActionHandler(action, available(action) ? detail => mediaActions.current?.(action, detail) : null); }
      catch { /* Individual actions vary by browser. */ }
    }
    return () => {
      session.metadata = null;
      session.playbackState = 'none';
      for (const action of actions) {
        try { session.setActionHandler(action, null); } catch { /* Unsupported action. */ }
      }
    };
  }, [episode?.id, episode?.title, episode?.topic, previous?.id, next?.id, locked]);

  useEffect(() => {
    const session = navigator.mediaSession;
    if (!session || !episode || locked) return;
    session.playbackState = audio.playing ? 'playing' : 'paused';
    if (Number.isFinite(audio.duration) && audio.duration > 0 && Number.isFinite(audio.position)) {
      session.setPositionState?.({ duration: audio.duration, position: Math.min(audio.position, audio.duration), playbackRate: 1 });
    }
  }, [audio.playing, audio.duration, audio.position, episode?.id, locked]);

  const toggle = () => audio.playing ? audio.pause() : audio.play();
  const chosen = (value: string) => {
    const option = scopeOptions.find(candidate => playbackScopeKey(candidate.scope) === value);
    if (option) chooseScope(option.scope);
  };
  const itemsForExport = order === 'ordered' ? eligible : orderIds.flatMap(id => {
    const item = eligible.find(candidate => candidate.id === id);
    return item ? [item] : [];
  });
  const exportItems = (items: PodcastEpisode[], name: string) => access
    ? access.export(items, name)
    : exportOfflinePlaylist(identity, items, name);

  return <PlayerContext.Provider value={{ play, open }}>
    {children}
    <audio ref={audio.element} preload="auto" hidden />
    {!locked && episode && <aside className={offline ? 'podcast-mini' : 'podcast-mini podcast-mini-online'} aria-label="播客迷你播放器">
      <button type="button" className="podcast-mini-title" onClick={open} aria-label={'打开播放器，' + episode.title}>
        <span aria-hidden="true">♫</span><span><strong>{episode.title}</strong><small>{podcastTime(audio.position)} / {podcastTime(audio.duration)}</small></span>
      </button>
      <button type="button" className="podcast-icon" onClick={toggle} disabled={audio.loading}
        aria-label={audio.playing ? '暂停' : '播放'}>{audio.playing ? '❚❚' : '▶'}</button>
      <button type="button" className="podcast-icon" onClick={() => step(1)}
        disabled={!next} aria-label="下一集">⏭</button>
    </aside>}
    <PodcastDialog name="播客播放器" open={visible && !locked} onClose={close}>
      <header className="podcast-player-header">
        <button type="button" className="podcast-icon" onClick={close} aria-label="收起播放器">⌄</button>
        <h2>通勤播客</h2>
        <button type="button" className="podcast-icon" onClick={() => setManaging(value => !value)}
          aria-label="离线和导出" aria-expanded={managing}>⋯</button>
      </header>
      {episode && <>
        <PodcastCards episode={episode} previous={previous} next={next} step={step} />
        <div className="podcast-now" aria-live="polite"><h3>{episode.title}</h3><span>{episode.question_code}</span></div>
        <div className="podcast-progress">
          <input type="range" min={0} max={audio.duration || 0} step={0.25} value={Math.min(audio.position, audio.duration)}
            disabled={!audio.duration} aria-label="播放进度" aria-valuetext={podcastTime(audio.position)}
            onChange={event => audio.seek(Number(event.target.value))} />
          <div><span>{podcastTime(audio.position)}</span><span>{podcastTime(audio.duration)}</span></div>
        </div>
        <div className="podcast-controls">
          <button type="button" className="podcast-icon" onClick={() => step(-1)} disabled={!previous} aria-label="上一集">⏮</button>
          <button type="button" className="podcast-icon podcast-play" onClick={toggle} disabled={audio.loading}
            aria-label={audio.playing ? '暂停' : '播放'}>{audio.playing ? '❚❚' : '▶'}</button>
          <button type="button" className="podcast-icon" onClick={() => step(1)} disabled={!next} aria-label="下一集">⏭</button>
        </div>
        <div className="podcast-familiarity" role="group" aria-label="这集我">
          <span>{episode.familiarity === 'unmarked' ? '这集我 · 未标记' : '这集我'}</span>
          <button type="button" onClick={() => mark('unfamiliar')} aria-pressed={episode.familiarity === 'unfamiliar'}>不熟悉</button>
          <button type="button" onClick={() => mark('familiar')} aria-pressed={episode.familiarity === 'familiar'}>熟悉</button>
        </div>
        <button type="button" onClick={() => void showTranscript()} aria-expanded={!!transcript} aria-controls="podcast-transcript">逐字稿</button>
        {transcript?.id === episode.id && <section className="podcast-transcript" id="podcast-transcript" aria-label="逐字稿">
          {transcript.text !== undefined ? <p>{transcript.text}</p> : <p role="status">正在读取逐字稿…</p>}
        </section>}
      </>}
      <div className="podcast-settings">
        <label>播放范围
          <select value={scopeValue} onChange={event => chosen(event.target.value)}>
            {scopeOptions.map(option => {
              const key = playbackScopeKey(option.scope);
              return <option key={key} value={key}>{option.label ?? option.name}</option>;
            })}
          </select>
        </label>
        <div className="podcast-modes" role="group" aria-label="播放方式">
          <button type="button" aria-pressed={!repeat && order === 'ordered'} onClick={() => chooseOrder('ordered')}>顺序</button>
          <button type="button" aria-pressed={!repeat && order === 'shuffle'} onClick={() => chooseOrder('shuffle')}>随机</button>
          <button type="button" aria-pressed={repeat} disabled={!episode}
            onClick={() => setRepeat(value => !value)}>单集循环</button>
        </div>
        {!episode && eligible.length > 0 && <button type="button" onClick={() => {
          const first = itemsForExport[0];
          if (first) pick(first, true);
        }}>播放当前范围</button>}
        {!eligible.length && <p className="podcast-empty">{noEpisodes}</p>}
      </div>
      {managing && <section className="podcast-offline-options" aria-label="离线和导出">
        {access && <button type="button" disabled={pending || !eligible.length}
          onClick={() => void operate(() => access.save(eligible, scopeName, scope), '已存到这台设备')}>保存当前范围到此设备</button>}
        {episode && <button type="button" disabled={pending}
          onClick={() => void operate(() => exportItems([episode], episode.title), '已发起 MP3 下载')}>导出这集 MP3</button>}
        <button type="button" disabled={pending || !itemsForExport.length}
          onClick={() => void operate(() => exportItems(itemsForExport, scopeName), '已发起下载')}>导出当前范围</button>
        {!!packs.length && !offline && <a href={offlinePodcastUrl}>打开离线播放器</a>}
        {packs.map(pack => <div className="podcast-pack" key={pack.id}>
          <span>{pack.name} · {pack.episodeIds.length} 集</span>
          <button type="button" disabled={pending} onClick={() => void operate(async () => {
            if (offline) setScope(allScope);
            await removePodcastPack(identity, pack.id);
            if (offline && episode && !(await readSavedMedia(identity, episode.id))) audio.lock();
            window.dispatchEvent(new Event(podcastPacksChanged));
          }, '已移除此设备上的副本')}>移除</button>
        </div>)}
      </section>}
      {audio.error && <p role="alert" className="podcast-status">{audio.error}</p>}
      {audio.loading && <p role="status" className="podcast-status">正在读取音频…</p>}
      {status && <p role="status" className="podcast-status">{status}</p>}
    </PodcastDialog>
  </PlayerContext.Provider>;
}
