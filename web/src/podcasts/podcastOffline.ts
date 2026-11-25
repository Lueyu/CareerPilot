import { getWorkspace, workspaceLogin } from '../api/client';
import { playbackScopeKey, type PlaybackScope } from './podcastQueue';
import { podcastApi, type PodcastEpisode } from '../api/podcasts';
import {
  identityIsCurrent, podcastIdentity, rememberOfflineIdentity, type PodcastIdentity,
} from './podcastOfflineIdentity';
import {
  readSavedMedia, savePodcastPack, type SavedMedia,
} from './podcastStore';

import {
  exportPodcastMedia, offlinePodcastUrl, podcastPacksChanged, readyEpisodes,
} from './podcastOfflineMedia';

export { offlinePodcastUrl, podcastLibraryChanged, podcastPacksChanged } from './podcastOfflineMedia';

export function currentPodcastIdentity() {
  return podcastIdentity(getWorkspace(), workspaceLogin);
}

function assertActiveWorkspace(identity: PodcastIdentity) {
  if (!identityIsCurrent(identity) || getWorkspace() !== identity.workspace) {
    throw new Error('工作区已切换，请重新打开节目库');
  }
}

async function prepareOfflineShell() {
  if (!window.isSecureContext || !navigator.serviceWorker || !window.indexedDB || !window.caches) {
    throw new Error('请通过受信任的 HTTPS 地址或本机地址打开 CareerPilot，再保存离线播客');
  }
  const registration = await navigator.serviceWorker.register('/podcast-sw.js', { scope: offlinePodcastUrl });
  await new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(() => finish(new Error('离线播放器准备超时，请重试')), 25000);
    const channel = new MessageChannel();
    const worker = registration.installing ?? registration.waiting ?? registration.active;
    function finish(error?: Error) {
      window.clearTimeout(timeout);
      channel.port1.close();
      worker?.removeEventListener('statechange', checkWorker);
      if (error) reject(error); else resolve();
    }
    function checkWorker() {
      if (worker?.state === 'redundant' || !worker) return finish(new Error('离线播放器无法启动'));
      if (worker.state !== 'activated') return;
      worker.removeEventListener('statechange', checkWorker);
      channel.port1.onmessage = ({ data }: MessageEvent<{ ready: boolean; error?: string }>) => {
        finish(data.ready ? undefined : new Error(data.error ?? '离线播放器准备失败'));
      };
      worker.postMessage({ type: 'PREPARE_PODCAST_SHELL' }, [channel.port2]);
    }
    worker?.addEventListener('statechange', checkWorker);
    checkWorker();
  });
}

export async function saveOffline(episodes: PodcastEpisode[], name: string, scope?: PlaybackScope): Promise<void> {
  const identity = currentPodcastIdentity();
  assertActiveWorkspace(identity);
  const ready = readyEpisodes(episodes);
  if (!ready.length) throw new Error('没有可保存的节目');
  if (ready.length !== new Set(episodes.map(episode => episode.id)).size) throw new Error('请等所有节目合成后再保存');
  await prepareOfflineShell();

  const media: SavedMedia[] = [];
  for (const episode of ready) {
    assertActiveWorkspace(identity);
    const audio = await podcastApi.audio(episode.id);
    assertActiveWorkspace(identity);
    if (!audio.size) throw new Error('节目音频为空，请重试');
    const { script } = await podcastApi.script(episode.id);
    assertActiveWorkspace(identity);
    media.push({ episode, audio, script });
  }

  const title = name.trim() || '离线歌单';
  const id = 'scope:' + JSON.stringify([
    scope ? playbackScopeKey(scope) : 'episodes',
    scope?.kind === 'selection' || !scope ? ready.map(episode => episode.id) : [],
  ]);
  await savePodcastPack(identity, { id, name: title, episodeIds: ready.map(episode => episode.id) }, media);
  assertActiveWorkspace(identity);
  rememberOfflineIdentity(identity);
  window.dispatchEvent(new Event(podcastPacksChanged));
}

export async function downloadPlaylist(episodes: PodcastEpisode[], name: string): Promise<void> {
  const identity = currentPodcastIdentity();
  assertActiveWorkspace(identity);
  await exportPodcastMedia(episodes, name, async id => {
    assertActiveWorkspace(identity);
    return (await readSavedMedia(identity, id).catch(() => undefined))?.audio ?? podcastApi.audio(id);
  }, () => assertActiveWorkspace(identity));
}
