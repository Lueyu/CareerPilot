import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { scopeEpisodes, type PlaybackLibrary } from './podcastQueue';
import '../styles/tokens.css';
import '../styles/base.css';
import { offlineLibrary, podcastPacksChanged } from './podcastOfflineMedia';
import {
  podcastStorageScope, readOfflineIdentity, verifyOfflineIdentity, watchPodcastIdentity, type PodcastIdentity,
} from './podcastOfflineIdentity';
import { PodcastPlaybackProvider, usePodcastPlayer } from './PodcastPlayback';
import { podcastTime } from './PodcastPlayerDialog';

function SavedPlaylists({ identity }: { identity: PodcastIdentity }) {
  const [library, setLibrary] = useState<PlaybackLibrary>();
  const [error, setError] = useState(false);
  const player = usePodcastPlayer();
  useEffect(() => {
    let request = 0;
    let closed = false;
    const refresh = () => {
      const latest = ++request;
      void offlineLibrary(identity).then(data => {
        if (!closed && latest === request) { setLibrary(data); setError(false); }
      }).catch(() => {
        if (!closed && latest === request) { setLibrary(undefined); setError(true); }
      });
    };
    refresh();
    window.addEventListener(podcastPacksChanged, refresh);
    return () => { closed = true; window.removeEventListener(podcastPacksChanged, refresh); };
  }, [identity]);

  return <main className="podcast-standalone">
    <header><div><p>CareerPilot</p><h1>离线播客</h1></div><a href="/podcasts">返回节目库</a></header>
    {error && <p role="alert">无法读取已保存的节目。</p>}
    {!library && !error && <p role="status">正在打开离线节目…</p>}
    {library && (library.playlists.length ? <>
      <button type="button" onClick={player.open}>打开播放器</button>
      {library.playlists.map(list => {
        const scope = { kind: 'playlist', id: list.id } as const;
        const queue = scopeEpisodes(library, scope);
        return <section key={list.id}>
          <h2>{list.name}</h2>
          <ol>{queue.map(episode => <li key={episode.id}>
            <button type="button" onClick={() => player.play(episode, queue, scope)}>
              <span>{episode.title}</span><small>{podcastTime(episode.duration_seconds)}</small>
            </button>
          </li>)}</ol>
        </section>;
      })}
    </> : <p>这台设备还没有离线歌单。联网打开节目库后，可以保存歌单。</p>)}
  </main>;
}

function OfflinePodcastEntry() {
  const [initial] = useState(readOfflineIdentity);
  const [identity, setIdentity] = useState<PodcastIdentity | null>();
  useEffect(() => {
    let controller: AbortController | undefined;
    function verify() {
      controller?.abort();
      const candidate = readOfflineIdentity();
      if (!initial || !candidate || initial.epoch !== candidate.epoch ||
          podcastStorageScope(initial) !== podcastStorageScope(candidate)) {
        setIdentity(null);
        return;
      }
      const check = new AbortController();
      controller = check;
      void verifyOfflineIdentity(candidate, check.signal).then(current => {
        if (!check.signal.aborted) setIdentity(current ? initial : null);
      }).catch(() => { if (!check.signal.aborted) setIdentity(null); });
    }
    const unwatch = watchPodcastIdentity(verify);
    window.addEventListener('focus', verify);
    window.addEventListener('online', verify);
    verify();
    return () => {
      controller?.abort();
      unwatch();
      window.removeEventListener('focus', verify);
      window.removeEventListener('online', verify);
    };
  }, [initial]);

  if (!identity) return <main className="podcast-standalone">
    <header><h1>离线播客</h1><a href="/podcasts">返回节目库</a></header>
    <p role="status">{identity === undefined ? '正在打开离线播放器…'
      : '当前工作区在这台设备上没有可打开的离线歌单。请联网并从节目库重新保存。'}</p>
  </main>;
  return <PodcastPlaybackProvider identity={identity}><SavedPlaylists identity={identity} /></PodcastPlaybackProvider>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><OfflinePodcastEntry /></StrictMode>);
